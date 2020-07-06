import random
import tempfile
import warnings
import sys

import arcgis
from arcgis.features import FeatureLayer

HAS_FASTAI = True
try:
    from fastai.tabular import TabularList
    from fastai.tabular import TabularDataBunch
    from fastai.tabular.transform import FillMissing, Categorify, Normalize
    from fastai.tabular import cont_cat_split, add_datepart
    import torch
except Exception as e:
    HAS_FASTAI = False

HAS_NUMPY = True
try:
    import numpy as np
except:
    HAS_NUMPY = False

HAS_SK_LEARN = True
try:
    from sklearn.pipeline import make_pipeline
    from sklearn.compose import make_column_transformer
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import Normalizer, LabelEncoder
except:
    HAS_SK_LEARN = False


class TabularDataObject(object):
    _categorical_variables = []
    _continuous_variables = []
    dependent_variables = []

    @classmethod
    def prepare_data_for_layer_learner(
        cls,
        input_features,
        dependent_variable,
        feature_variables=None,
        raster_variables=None,
        date_field=None,
        distance_feature_layers=None,
        procs=None,
        val_split_pct=0.1,
        seed=42,
        batch_size=64
    ):

        if not HAS_FASTAI:
            return

        feature_variables = feature_variables if feature_variables else []
        raster_variables = raster_variables if raster_variables else []

        tabular_data = cls()
        tabular_data._dataframe, tabular_data._field_mapping = TabularDataObject._prepare_dataframe_from_features(
            input_features,
            dependent_variable,
            feature_variables,
            raster_variables,
            date_field,
            distance_feature_layers
        )

        tabular_data._dataframe = tabular_data._dataframe.reindex(sorted(tabular_data._dataframe.columns), axis=1)

        tabular_data._categorical_variables = tabular_data._field_mapping['categorical_variables']
        tabular_data._continuous_variables = tabular_data._field_mapping['continuous_variables']
        tabular_data._dependent_variable = tabular_data._field_mapping['dependent_variable']

        tabular_data._procs = procs
        tabular_data._val_split_pct = val_split_pct
        tabular_data._bs = batch_size
        tabular_data._seed = seed

        random.seed(seed)
        validation_indexes = random.sample(range(len(tabular_data._dataframe)), round(val_split_pct * len(tabular_data._dataframe)))
        tabular_data._validation_indexes = validation_indexes

        tabular_data._training_indexes = list(set([i for i in range(len(tabular_data._dataframe))]) - set(validation_indexes))
        tabular_data._is_empty = False
        tabular_data._is_classification = tabular_data._is_classification()
        return tabular_data

    def _prepare_validation_databunch(self, dataframe):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            kwargs_variables = {'num_workers': 0} if sys.platform == 'win32' else {}
            # kwargs_variables['tfm_y'] = True
            fm = FillMissing(self._categorical_variables, self._continuous_variables)
            fm.add_col = False
            fm(dataframe)
            databunch_half = TabularList.from_df(
                dataframe,
                path=tempfile.NamedTemporaryFile().name,
                cat_names=self._categorical_variables,
                cont_names=self._continuous_variables,
                procs=[Categorify, Normalize]
            ).split_by_idx([i for i in range(int(len(dataframe)/2))]).label_empty().databunch(**kwargs_variables)

            databunch_second_half = TabularList.from_df(
                dataframe,
                path=tempfile.NamedTemporaryFile().name,
                cat_names=self._categorical_variables,
                cont_names=self._continuous_variables,
                procs=[Categorify, Normalize]
            ).split_by_idx([i for i in range(int(len(dataframe)/2), len(dataframe))]).label_empty().databunch(**kwargs_variables)

        return databunch_half, databunch_second_half

    def _is_classification(self):
        if self._is_empty:
            return True

        if not HAS_NUMPY:
            raise Exception("This module requires numpy.")

        df = self._dataframe
        labels = df[self._dependent_variable]

        if labels.isna().sum().sum() != 0:
            raise Exception("You have some missing values in dependent variable column.")

        labels = np.array(labels)

        from numbers import Integral
        if isinstance(labels[0], (float, np.float32)):
            return False

        if isinstance(int(labels[0]), (str, Integral)):
            return True

    @property
    def _databunch(self):
        if self._is_empty:
            return None

        return TabularDataObject._prepare_databunch(
            self._dataframe,
            self._field_mapping,
            self._procs,
            self._validation_indexes,
            self._bs
        )

    @property
    def _ml_data(self):
        if self._is_empty:
            return None, None, None, None

        if not HAS_NUMPY:
            raise Exception("This module requires numpy.")

        if not HAS_SK_LEARN:
            raise Exception("This module requires scikit-learn.")

        dataframe = self._dataframe

        labels = np.array(dataframe[self._dependent_variable])

        dataframe = dataframe.drop(self._dependent_variable, axis=1)

        if not self._procs:
            numerical_transformer = make_pipeline(
                SimpleImputer(strategy='median'),
                Normalizer())

            categorical_transformer = make_pipeline(
                SimpleImputer(strategy='constant')
            )

            _procs = make_column_transformer(
                (numerical_transformer, self._continuous_variables),
                (categorical_transformer, self._categorical_variables))
        else:
            _procs = self._procs

        self._encoder_mapping = None
        if self._categorical_variables:
            mapping = {}
            for variable in self._categorical_variables:
                labelEncoder = LabelEncoder()
                dataframe[variable] = labelEncoder.fit_transform(dataframe[variable])
                mapping[variable] = labelEncoder
            self._encoder_mapping = mapping

        processed_data = _procs.fit_transform(dataframe)

        training_data = processed_data.take(self._training_indexes, axis=0)
        training_labels = labels.take(self._training_indexes)
        validation_data = processed_data.take(self._validation_indexes, axis=0)
        validation_labels = labels.take(self._validation_indexes)

        return training_data, training_labels, validation_data, validation_labels

    def _process_data(self, dataframe):
        if not HAS_NUMPY:
            raise Exception("This module requires numpy.")

        if not HAS_SK_LEARN:
            raise Exception("This module requires scikit-learn.")

        if not self._procs:
            numerical_transformer = make_pipeline(
                SimpleImputer(strategy='median'),
                Normalizer())

            categorical_transformer = make_pipeline(
                SimpleImputer(strategy='constant')
            )

            _procs = make_column_transformer(
                (numerical_transformer, self._continuous_variables),
                (categorical_transformer, self._categorical_variables))
        else:
            _procs = self._procs

        if self._encoder_mapping:
            for variable, encoder in self._encoder_mapping.items():
                dataframe[variable] = encoder.fit_transform(np.array(dataframe[variable], dtype='int64'))

        processed_data = _procs.fit_transform(dataframe)

        return processed_data

    def show_batch(self):
        """
        Shows a batch of dataframe prepared without applying transforms.
        """

        random_batch = random.sample(self._training_indexes, self._bs)
        return self._dataframe.loc[random_batch].reset_index(drop=True)

    @staticmethod
    def _prepare_dataframe_from_features(
            input_features,
            dependent_variable,
            feature_variables=None,
            raster_variables=None,
            date_field=None,
            distance_feature_layers=None
    ):
        feature_variables = feature_variables if feature_variables else []
        raster_variables = raster_variables if raster_variables else []
        distance_feature_layers = distance_feature_layers if distance_feature_layers else []

        continuous_variables = []
        categorical_variables = []
        for field in feature_variables:
            if isinstance(field, tuple):
                if field[1]:
                    categorical_variables.append(field[0])
                else:
                    continuous_variables.append(field[0])
            else:
                continuous_variables.append(field)

        rasters = []
        for raster in raster_variables:
            if isinstance(raster, tuple):
                rasters.append(raster[0])
                if raster[1]:
                    categorical_variables.append(raster[0].name)
                else:
                    continuous_variables.append(raster[0].name)
            else:
                rasters.append(raster)
                continuous_variables.append(raster.name)

        dataframe = TabularDataObject._process_layer(
            input_features,
            date_field,
            distance_feature_layers,
            raster_variables
        )

        dataframe_columns = dataframe.columns
        if distance_feature_layers:
            count = 1
            while f'NEAR_DIST_{count}' in dataframe_columns:
                continuous_variables.append(f'NEAR_DIST_{count}')
                count = count + 1

        fields_to_keep = continuous_variables + categorical_variables + [dependent_variable]

        for column in dataframe_columns:
            if column not in fields_to_keep:
                dataframe = dataframe.drop(column, axis=1)
            elif column == dependent_variable:
                continue
            elif column in categorical_variables and dataframe[column].dtype == float:
                warnings.warn(f"Changing column {column} to continuous")
                categorical_variables.remove(column)
                continuous_variables.append(column)
            elif column in categorical_variables and dataframe[column].unique().shape[0] > 20:
                warnings.warn(f"Column {column} has more than 20 unique value. Sure this is categorical?")

        if date_field:
            date_fields = [
                ('Year', True), ('Month', True), ('Week', True),
                ('Day', True), ('Dayofweek', True), ('Dayofyear', False),
                ('Is_month_end', True), ('Is_month_start', True),
                ('Is_quarter_end', True), ('Is_quarter_start', True),
                ('Is_year_end', True), ('Is_year_start', True),
                ('Hour', True), ('Minute', True), ('Second', True), ('Elapsed', False)]

            for field in date_fields:
                if field[0] in dataframe_columns:
                    if field[1]:
                        categorical_variables.append(field[0])
                    else:
                        continuous_variables.append(field[1])

        return dataframe, {'dependent_variable': dependent_variable,
                           'categorical_variables': categorical_variables if categorical_variables else [],
                           'continuous_variables': continuous_variables if continuous_variables else []}

    @staticmethod
    def _process_layer(input_features, date_field, distance_layers, rasters):

        if isinstance(input_features, FeatureLayer):
            input_layer = input_features
            sdf = input_features.query().sdf
        else:
            sdf = input_features
            input_layer = sdf.spatial.to_feature_collection()

        if distance_layers:
            # Use proximity tool
            print("Calculating Distances.")
            count = 1
            for distance_layer in distance_layers:
                output = arcgis.features.use_proximity.find_nearest(input_layer, distance_layer, max_count=1)
                connecting_df = output['connecting_lines_layer'].query().sdf
                near_dist = []

                for i in range(len(connecting_df)):
                    near_dist.append(connecting_df.iloc[i]['Total_Miles'])

                sdf[f'NEAR_DIST_{count}'] = near_dist
                count = count + 1

        # Process Raster Data to get information.
        rasters_data = {}

        original_points = []
        for i in range(len(sdf)):
            original_points.append(sdf.iloc[i]["SHAPE"])

        input_layer_spatial_reference = sdf.spatial._sr
        for raster in rasters:
            raster_type = 0

            if isinstance(raster, tuple):
                if raster[1] is True:
                    raster_type = 1
                raster = raster[0]
            rasters_data[raster.name] = []

            shape_objects_transformed = arcgis.geometry.project(original_points, input_layer_spatial_reference,
                                                                raster.extent['spatialReference'])
            for shape in shape_objects_transformed:
                shape['spatialReference'] = raster.extent['spatialReference']
                if isinstance(shape, arcgis.geometry._types.Point):
                    raster_value = raster.read(origin_coordinate=(shape['x'], shape['y']), ncols=1, nrows=1)
                    value = raster_value[0][0][0]
                elif isinstance(shape, arcgis.geometry._types.Polygon):
                    xmin, ymin, xmax, ymax = shape.extent
                    start_x, start_y = xmin + (raster.mean_cell_width / 2), ymin + (raster.mean_cell_height / 2)
                    values = []
                    while start_y < ymax:
                        while start_x < xmax:
                            if shape.contains(arcgis.geometry._types.Point(
                                    {'x': start_x, 'y': start_y, 'sr': raster.extent['spatialReference']})):
                                values.append(raster.read(origin_coordinate=(start_x - raster.mean_cell_width, start_y), ncols=1, nrows=1)[0][0][0])
                            start_x = start_x + raster.mean_cell_width
                        start_y = start_y + raster.mean_cell_height
                        start_x = xmin + (raster.mean_cell_width / 2)

                    if len(values) == 0:
                        values.append(raster.read(origin_coordinate=(shape.true_centroid['x'] - raster.mean_cell_width, shape.true_centroid['y']), ncols=1,
                                        nrows=1)[0][0][0])
                    if raster_type == 0:
                        value = sum(values) / len(values)
                    else:
                        value = max(values, key=values.count)
                else:
                    raise Exception("Input features can be point or polygon only.")

                rasters_data[raster.name].append(value)

        # Append Raster data to sdf
        for key, value in rasters_data.items():
            sdf[key] = value

        if date_field:
            try:
                add_datepart(sdf, date_field)
            except:
                pass

        return sdf

    @staticmethod
    def _prepare_databunch(
        dataframe,
        fields_mapping,
        procs=None,
        validation_indexes=[],
        batch_size=64
    ):

        if procs is None:
            procs = [Categorify, Normalize]
            fm = FillMissing(fields_mapping['categorical_variables'], fields_mapping['continuous_variables'])
            fm.add_col = False
            fm(dataframe)

        temp_file = tempfile.NamedTemporaryFile().name

        kwargs_variables = {'num_workers': 0} if sys.platform == 'win32' else {}

        kwargs_variables['cat_names'] = fields_mapping['categorical_variables']
        kwargs_variables['cont_names'] = fields_mapping['continuous_variables']
        kwargs_variables['bs'] = batch_size

        if hasattr(arcgis, "env") and getattr(arcgis.env, "_processorType", "") == "CPU":
            kwargs_variables["device"] = torch.device('cpu')

        data_bunch = TabularDataBunch.from_df(
            temp_file,
            dataframe,
            fields_mapping['dependent_variable'],
            procs=procs,
            valid_idx=validation_indexes,
            **kwargs_variables
        )

        return data_bunch

    @classmethod
    def _empty(cls, categorical_variables, continuous_variables, dependent_variable, encoder_mapping, procs=None):
        class_object = cls()
        class_object._dependent_variable = dependent_variable
        class_object._continuous_variables = continuous_variables
        class_object._categorical_variables = categorical_variables
        class_object._encoder_mapping = encoder_mapping
        class_object._is_empty = True
        class_object._procs = procs

        return class_object