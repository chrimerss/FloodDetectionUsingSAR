"""
Provides functions to gather usage statistics for Portal/ArcGIS Online
"""
import os
import datetime
from .._impl._con import Connection
from ..._impl.common._mixins import PropertyMap
from ..._impl.common._utils import local_time_to_online, timestamp_to_datetime
from ...gis import GIS
from ._base import BasePortalAdmin
########################################################################
class AGOLUsageReports(BasePortalAdmin):
    """
    Compiles Simple Usage Reports from ArcGIS Online
    """
    _json_dict = {}
    _json = None
    _con = None
    _portal = None
    _gis = None
    _url = None
    #----------------------------------------------------------------------
    def _init(self, connection=None):
        """loads the properties into the class"""
        self._json_dict = {}
        self._properties = PropertyMap(self._json_dict)
    #----------------------------------------------------------------------
    def credit(self,
                start_time=None,
                time_frame="week",
                export=False):
        """
        Creates a Report as a Panda's dataframe or CSV file for a given time range
        for ArcGIS Online Organizations.

        ===============     ====================================================
        **Argument**        **Description**
        ---------------     ----------------------------------------------------
        start_time          optional datetime, the time to step back from.  If
                            None, the current time is used.
        ---------------     ----------------------------------------------------
        time_frame          optional string, is the timeframe report to create.
                            Allowed values: today, week, 14days, 30days, 60days,
                            90days, 6months, year
        ---------------     ----------------------------------------------------
        export              optional boolean, if True, a csv is generated from
                            the request. If False, a Panda's dataframe is
                            returned
        ===============     ====================================================

        :return:
             string path to csv file or Panda's Dataframe (default)
        """
        out_folder = None
        if start_time is None:
            start_time = datetime.datetime.now()
        if isinstance(start_time, datetime.datetime) == False:
            raise ValueError("start_time and end_time must be datetime objects")
        end_time = start_time - datetime.timedelta(days=1)
        period = '1h'
        if time_frame.lower() in ['7days', 'week']:
            end_time = start_time - datetime.timedelta(days=7)
            period = '1d'
        elif time_frame.lower() == '14days':
            end_time = start_time - datetime.timedelta(days=14)
            period = '1d'
        elif time_frame.lower() == '30days':
            end_time = start_time - datetime.timedelta(days=30)
            period = '1d'
        elif time_frame.lower() == '60days':
            end_time = start_time - datetime.timedelta(days=60)
            period = '1d'
        elif time_frame.lower() == '90days':
            end_time = start_time - datetime.timedelta(days=90)
            period = '1d'
        elif time_frame.lower() == '6months':
            end_time = start_time - datetime.timedelta(days=180)
            period = '1w'
        elif time_frame.lower() == 'year':
            end_time = start_time - datetime.timedelta(days=365)
            period = '1m'
        # Convert to timestamps
        end_time = str(int(local_time_to_online(dt=end_time)))
        start_time = str(int(local_time_to_online(dt=start_time)))
        f = 'json'
        if export:
            f = 'csv'
        if export and \
             (out_folder is None or \
              os.path.isdir(out_folder) == False):
            import tempfile
            out_folder = tempfile.gettempdir()
        params = {
              'f' : f,
               'startTime' : end_time,
               'endTime' : start_time,
               'vars' : 'credits,num',
               'groupby' : 'stype,etype',
               'period' : period
          }
        res = self._con.post(path=self._url,
                               postdata=params)
        if export:
            return res
        elif isinstance(res, (dict, PropertyMap)):
            import pandas as pd
            data = res['data'][0]['credits']
            for row in data:
                if isinstance(row[0], str):
                    row[0] = int(row[0])
                row[0] = timestamp_to_datetime(timestamp=row[0])

            df = pd.DataFrame.from_records(data=data, columns=['date', 'credits'], coerce_float=True)
            df['credits'] = df['credits'].astype(float)
            return df
        return res
    #----------------------------------------------------------------------
    def users(self,
               start_time=None,
                      time_frame="week"):
        """
        Creates a usage report for all users for a given organization on
        ArcGIS Online.

        ===============     ====================================================
        **Argument**        **Description**
        ---------------     ----------------------------------------------------
        start_time          optional datetime, the time to step back from.  If
                            None, the current time is used.
        ---------------     ----------------------------------------------------
        time_frame          optional string, is the timeframe report to create.
                            Allowed values: today, week, 14days, 30days, 60days,
                            90days, 6months, year
        ===============     ====================================================

        :return:
             dictionary
        """
        out_folder = None
        if start_time is None:
            start_time = datetime.datetime.now()
        if isinstance(start_time, datetime.datetime) == False:
            raise ValueError("start_time and end_time must be datetime objects")
        end_time = start_time - datetime.timedelta(days=1)
        period = '1h'
        if time_frame.lower() in ['7days', 'week']:
            end_time = start_time - datetime.timedelta(days=7)
            period = '1d'
        elif time_frame.lower() == '14days':
            end_time = start_time - datetime.timedelta(days=14)
            period = '1d'
        elif time_frame.lower() == '30days':
            end_time = start_time - datetime.timedelta(days=30)
            period = '1d'
        elif time_frame.lower() == '60days':
            end_time = start_time - datetime.timedelta(days=60)
            period = '1d'
        elif time_frame.lower() == '90days':
            end_time = start_time - datetime.timedelta(days=90)
            period = '1d'
        elif time_frame.lower() == '6months':
            end_time = start_time - datetime.timedelta(days=180)
            period = '1w'
        elif time_frame.lower() == 'year':
            end_time = start_time - datetime.timedelta(days=365)
            period = '1m'
        # Convert to timestamps
        end_time = str(int(local_time_to_online(dt=end_time)))
        start_time = str(int(local_time_to_online(dt=start_time)))
        params = {
              'f' : 'json',
               'startTime' : end_time,
               'endTime' : start_time,
               'vars' : 'credits',
               'groupby' : 'username,userorgid',
               'period' : period
          }
        res = self._con.post(path=self._url,
                               postdata=params)
        return res
    #----------------------------------------------------------------------
    def applications(self, start_time=None, time_frame='week'):
        """
        Creates a usage report for all registered application logins for a
        given organization on ArcGIS Online.

        ===============     ====================================================
        **Argument**        **Description**
        ---------------     ----------------------------------------------------
        start_time          optional datetime, the time to step back from.  If
                            None, the current time is used.
        ---------------     ----------------------------------------------------
        time_frame          optional string, is the timeframe report to create.
                            Allowed values: today, week, 14days, 30days, 60days,
                            90days, 6months, year
        ===============     ====================================================

        :return:
             dictionary
        """
        out_folder = None
        if start_time is None:
            start_time = datetime.datetime.now()
        if isinstance(start_time, datetime.datetime) == False:
            raise ValueError("start_time and end_time must be datetime objects")
        end_time = start_time - datetime.timedelta(days=1)
        period = '1h'
        if time_frame.lower() in ['7days', 'week']:
            end_time = start_time - datetime.timedelta(days=7)
            period = '1d'
        elif time_frame.lower() == '14days':
            end_time = start_time - datetime.timedelta(days=14)
            period = '1d'
        elif time_frame.lower() == '30days':
            end_time = start_time - datetime.timedelta(days=30)
            period = '1d'
        elif time_frame.lower() == '60days':
            end_time = start_time - datetime.timedelta(days=60)
            period = '1d'
        elif time_frame.lower() == '90days':
            end_time = start_time - datetime.timedelta(days=90)
            period = '1d'
        elif time_frame.lower() == '6months':
            end_time = start_time - datetime.timedelta(days=180)
            period = '1w'
        elif time_frame.lower() == 'year':
            end_time = start_time - datetime.timedelta(days=365)
            period = '1m'
        # Convert to timestamps
        end_time = str(int(local_time_to_online(dt=end_time)))
        start_time = str(int(local_time_to_online(dt=start_time)))
        params = {
              'f' : 'json',
                    'startTime' : end_time,
                    'endTime' : start_time,
                    'vars' : 'num',
                    'groupby' : 'appId',
                    'eType' : 'svcusg',
                    'sType' : 'applogin',
                    'period' : period
          }
        res = self._con.post(path=self._url,
                               postdata=params)
        return res
    #----------------------------------------------------------------------
    def _custom(self, start_time,
                 end_time, vars=None,
                period=None, groupby=None,
                name=None, stype=None,
                etype=None, appId=None,
                device_id=None, username=None,
                app_org_id=None, user_org_id=None,
                host_org_id=None):
        """
        returns the usage statistics value
        """
        if isinstance(start_time, datetime.datetime) == False or \
             isinstance(end_time, datetime.datetime) == False:
            raise ValueError("start_time and end_time must be datetime objects")

        url = self._url

        start_time = str(int(local_time_to_online(dt=start_time)))
        end_time = str(int(local_time_to_online(dt=end_time)))

        params = {
              'f' : 'json',
             'startTime' : end_time,
             'endTime' : start_time,
             'vars' : vars,
             'period' : period,
             'groupby' : groupby,
             'name' : name,
             'stype' : stype,
             'etype' : etype,
             'appId' : appId,
             'deviceId' : device_id,
             'username' : username,
             'appOrgId' : app_org_id,
             'userOrgId' : user_org_id,
             'hostOrgId' : host_org_id,
          }

        params = {key:item for key,item in params.items() if item is not None}
        return self._con.post(path=url,
                                postdata=params)
