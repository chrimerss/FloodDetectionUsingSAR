try:
    import spacy
    from spacy.gold import offsets_from_biluo_tags as _offsets_from_biluo_tags
    from spacy.gold import iob_to_biluo as _iob_to_biluo
    import pandas as pd
    import numpy as np
    HAS_SPACY = True
except:
    HAS_SPACY = False
from pathlib import Path
import json,random,os,tempfile,logging

__all__=["_from_iob_tags","_from_json","ner_prepare_data","_create_zip","even_mults"]

def _raise_spacy_import_error():
    raise Exception('This module requires pandas and spacy version 2.1.8. Install it using \"pip install pandas spacy==2.1.8\"')

def _create_zip(zipname, path):
    import shutil

    if os.path.exists(os.path.join(path, zipname) + '.zip'):
        os.remove(os.path.join(path, zipname) + '.zip')
        
    temp_dir = tempfile.TemporaryDirectory().name    
    zip_file = shutil.make_archive(os.path.join(temp_dir, zipname), 'zip', path)
    
    shutil.move(zip_file, path)

def even_mults(start:float, stop:float, n:int): #Taken from FastAI(https://github.com/fastai/fastai/blob/master/fastai/core.py#L150)
    "Build log-stepped array from `start` to `stop` in `n` steps."
    mult = stop/start
    step = mult**(1/(n-1))
    return np.array([start*(step**i) for i in range(n)])

def _from_iob_tags(tokens_collection, tags_collection):
    """
    Converts training data from ``IOB`` format to spacy offsets.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    tokens_collection       Required [list]. List of token lists
                            Example: [[This,is,a,test],[This,is,a,test1]]
    ---------------------   -------------------------------------------
    tags_collection         Required [list]. List of tag lists
                            Example: [[B-tagname,O,O,O],[B-tagname,I-tagname,O,O]]
    =====================   ===========================================
    """


    nlp=spacy.blank('en')
    
    train_data = [] 
    for tags, tokens in zip(tags_collection, tokens_collection):
        
        try:
            tags = _iob_to_biluo(tags)

            doc = spacy.tokens.doc.Doc(
            nlp.vocab, words = tokens, spaces = [True]*(len(tokens)-1)+[False])
            # run the standard pipeline against it
            for name, proc in nlp.pipeline:
                doc = proc(doc)
            
            text=' '.join(tokens)
            tags = _offsets_from_biluo_tags(doc, tags)
            train_data.append((text,{'entities':tags}))
        except:
            pass
        

    return train_data
        


def _from_json(path, text_key='text', offset_key='labels'):
    """
    Converts training data from JSON format to spacy offsets.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    text_key                Optional:str='text. Json key under which text is available
    ---------------------   -------------------------------------------
    offset_key              Optional:str='labels. Json key under which offsets are available
    =====================   ===========================================
    json-schema:
    ----------
    {"id": 1, "text": "EU rejects ...", "labels": [[0,2,"ORG"], [11,17, "MISC"], [34,41,"ORG"]]}
    {"id": 2, "text": "Peter Blackburn", "labels": [[0, 15, "PERSON"]]}
    {"id": 3, "text": "President Obama", "labels": [[10, 15, "PERSON"]]}
    ----------
    returns: A json file that can be consumed by ner_databunch.
    """
    
    train_data = []
    with open(path,'r', encoding='UTF-8') as f:
        data_list = f.readlines()
    for i, item in enumerate(data_list):
        try:
            train_data.append((json.loads(item).get(text_key), {'entities':json.loads(item).get(offset_key)}))
        except:
            pass
        
    return train_data

def ner_prepare_data(dataset_type, path, batch_size, class_mapping=None, val_split_pct=0.1):

    """
    Prepares a data object

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    dataset_type            Required string. ['ner_json', 'IOB', 'BILUO']
    ---------------------   -------------------------------------------
    address_tag             Optional dict. Address field/tag name 
                            in the training data.
    val_split_pct           Optional Float. Percentage of training data to keep
                            as validation. The default value is 0.1.
    =====================   ===========================================
    returns: A list [text,{entities},text,{entities}] that can be ingested by ``EntityRecognizer``.
    """
    import spacy
    v_list=spacy.__version__.split('.')
    version=sum([int(j)*10**(2*i) for i,j in enumerate(v_list[::-1])])
    if version<20108: #checking spacy version
        return logging.error(f'Entity recognition model needs spacy version 2.1.8 or higher. Your current spacy version is {spacy.__version__}, please update using \'pip install')

    if not HAS_SPACY:
        _raise_spacy_import_error()
    path=Path(path)
    if class_mapping:
        address_tag=class_mapping.get('address_tag')
    
    else:
        address_tag='Address'

    if dataset_type == 'ner_json':
        train_data = _from_json(path=path)
        path=path.parent
    elif dataset_type == 'BIO' or dataset_type == 'IOB':
        tags_collection = []
        tokens_collection = []
        tags_df = pd.read_csv(path/'tags.csv')
        tokens_df = pd.read_csv(path/'tokens.csv')
        
        for i,tags in tags_df.iterrows():
            tags_collection.append(list(tags.dropna()))
        
        for i,tokens in tokens_df.iterrows():
            tokens_collection.append(list(tokens.dropna()))

        train_data = _from_iob_tags(tags_collection=tags_collection, tokens_collection=tokens_collection)
    elif dataset_type == 'LBIOU' or dataset_type == 'BILUO':

        tags_collection = []
        tokens_collection = []
        tags_df = pd.read_csv(path/'tags.csv')
        tokens_df = pd.read_csv(path/'tokens.csv')
        train_data = []

        for i,tags in tags_df.iterrows():
            tags_collection.append(list(tags.dropna()))
        
        for i,tokens in tokens_df.iterrows():
            tokens_collection.append(list(tokens.dropna()))
    
        nlp=spacy.blank('en')
        train_data = [] 
        for tags, tokens in zip(tags_collection, tokens_collection):
            try:
                tags = _iob_to_biluo(tags)

                doc = spacy.tokens.doc.Doc(
                nlp.vocab, words = tokens, spaces = [True]*(len(tokens)-1)+[False])
                # run the standard pipeline against it
                for name, proc in nlp.pipeline:
                    doc = proc(doc) 
                text=' '.join(tokens)
                tags = _offsets_from_biluo_tags(doc, tags)
                train_data.append((text,{'entities':tags}))
            except:
                pass        
    data=DatabunchNER(train_data, val_split_pct=val_split_pct,batch_size=batch_size,address_tag=address_tag, test_ds=None)
    data.path=path
    return data

class _NERItemlist():
    """
    Creates a dataset to store data within ``ner_databunch`` object.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    batch_size              Batch size. 
    ---------------------   -------------------------------------------
    data                    Required:DatabunchNER. 
    =====================   ===========================================

    :returns: dataset.
    """

    
    def __init__(self, batch_size, data):
        self.batch_size = batch_size
        self.entities = list({i[2] for i in pd.concat([pd.Series(i['entities']) for i in [o[1] for o in data]])}) ##Extracting all the unique entity names from input json
        self.data = data
        self.x = [o[0] for o in data]
        self.y = [o[1] for o in data]

    def __getitem__(self, i):
        return self.data[i]

    def __len__(self):
        return len(self.data)

    def _random_batch(self, data):
        res = []
        for j in range(self.batch_size): 
            res.append(random.choice(data)) 
        return res    
    
    def _entities_to_dataframe(self, item):
        """
        This function is used to create pandas dataframe from training input data json.
        """
        text = item[0]
        df = pd.DataFrame(item[1].get('entities'))

        out_dict = {}
        for x in df[2].unique(): out_dict[x] = df[df[2] == x][[0, 1]].values.tolist()

        out = {}
        out['text'] = text
        for key in out_dict.keys():
            
            for tpl in out_dict.get(key):
                if out.get(key) == None:
                    out[key] = []
                out[key].append(text[tpl[0]:tpl[1]])
        return pd.Series(out)


    def show_batch(self):
        """
        This function shows a batch from the _NERItemlist.
        """
        data = self._random_batch(self.data)
        lst = []
        for item in data:
            lst.append(self._entities_to_dataframe(item))
        batch_df = pd.concat(lst,axis=1,sort=True).T
        batch_df

        return batch_df.fillna('')

    

class DatabunchNER():


    """
    Creates a databunch object.

    =====================   ===========================================
    **Argument**            **Description**
    ---------------------   -------------------------------------------
    ds                      Required data list returned from _ner_prepare_data(). 
    ---------------------   -------------------------------------------
    val_split_pct           Optional float. Percentage of training data to keep
                            as validation.
                            The default Value is 0.1.
    ---------------------   -------------------------------------------
    batch_size              Optional integer. Batch size
                            The default value is 5.                       
    =====================   ===========================================

    :returns: dataset
    """

    def __init__(self, ds, val_split_pct, batch_size, test_ds=None,address_tag=None):
        random.shuffle(ds)
        self.train_ds = _NERItemlist(batch_size,data = ds[:int(len(ds)*(1-val_split_pct))]) #creating an _NERItemlist with training dataset
        self.val_ds = _NERItemlist(batch_size,data = ds[int(len(ds)*(1-val_split_pct)):]) #creating an _NERItemlist with validation dataset
        self.entities=list(set(self.train_ds.entities).union(set(self.val_ds.entities)))
        self._address_tag=address_tag
        self._has_address=True
        self.batch_size=batch_size
        if self.batch_size>len(self.train_ds):
            return logging.error(f"Number of training data items ({len(self.train_ds)}) is less than the batch size ({self.batch_size}). Please get more training data or lower the batch size")        
        if self._address_tag not in self.entities:
            self._has_address=False
            return logging.warning("No Address tag found in your data.\n\
                1. If your data has an address field, pass your address field name as address tag in class mapping \n\
                e.g. - data=prepare_data(dataset_type=ds_type,path=training_data_folder,\n\t\t\t\
                    class_mapping={address_tag:address_field_name})\n\
                2. Else no action is required, if your data does not have any address information.")

    def show_batch(self):
        return self.train_ds.show_batch()
    