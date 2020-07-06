"""
This resource is a collection of all the usage reports created within
your site. The Create Usage Report operation lets you define a new
usage report.
"""
from __future__ import absolute_import
from __future__ import print_function
import json
import six
from .._common import BaseServer

########################################################################
class ReportManager(BaseServer):
    """
    A utility class for managing usage reports for ArcGIS Server.

    """
    _con = None
    _json_dict = None
    _url = None
    _json = None
    _metrics = None
    _reports = None
    #----------------------------------------------------------------------
    def __init__(self,
                 url,
                 gis,
                 initialize=False):
        """Constructor

        ==================     ====================================================================
        **Argument**           **Description**
        ------------------     --------------------------------------------------------------------
        url                    Required string. The machine URL.
        ------------------     --------------------------------------------------------------------
        gis                    Optional string. The GIS or Server object.
        ------------------     --------------------------------------------------------------------
        initialize             Optional string. Denotes whether to load the machine properties at
                               creation (True). Default is False.
        ==================     ====================================================================

        """
        super(ReportManager, self).__init__(url=url, gis=gis)
        if url.lower().endswith('/usagereports'):
            self._url = url
        else:
            self._url = url + "/usagereports"
        self._con = gis
        if initialize:
            self._init(gis)
    #----------------------------------------------------------------------
    def __str__(self):
        return '<%s at %s>' % (type(self).__name__, self._url)
    #----------------------------------------------------------------------
    def __repr__(self):
        return '<%s at %s>' % (type(self).__name__, self._url)
    #----------------------------------------------------------------------
    def list(self):
        """Retrieves a list of reports on the server.

        :return:
            A list of reports found.

        """
        if self.properties is None:
            self._init()
        self._reports = []
        if isinstance(self.properties['metrics'], list):
            for r in self.properties['metrics']:
                url = self._url + "/%s" % six.moves.urllib.parse.quote(r['reportname'])
                self._reports.append(Report(url=url,
                                            gis=self._con))
                del url
        return self._reports
    #----------------------------------------------------------------------
    @property
    def settings(self):
        """
        Gets the current usage reports settings. The usage reports
        settings are applied to the entire site. When usage
        reports are enabled, service usage statistics are collected and
        persisted to a statistics database. When usage reports are
        disabled, the statistics are not collected. The interval
        parameter defines the duration (in minutes) during which the usage
        statistics are sampled or aggregated (in-memory) before being
        written out to the statistics database. Database entries are
        deleted after the interval specified in the max_history parameter (
        in days), unless the max_history parameter is 0, for which the
        statistics are persisted forever.
        """
        params = {
            "f" : "json"
        }
        url = self._url + "/settings"
        return self._con.get(path=url,
                             params=params)
    #----------------------------------------------------------------------
    def edit(self,
             interval,
             enabled=True,
             max_history=0):
        """
        Edits the usage reports settings that are applied to the entire site.

        ==================     ====================================================================
        **Argument**           **Description**
        ------------------     --------------------------------------------------------------------
        interval               Required string. Defines the duration (in minutes) for which the
                               usage statistics are aggregated or sampled, in-memory, before being
                               written out to the statistics database.
        ------------------     --------------------------------------------------------------------
        enabled                Optional string. When usage reports are enabled, service
                               usage statistics are collected and persisted to a statistics
                               database. When usage reports are disabled, the statistics are not
                               collected.  The default is True (enabled).
        ------------------     --------------------------------------------------------------------
        max_history            Optional integer. The number of days after which usage statistics
                               are deleted from the statistics database. If the max_history
                               parameter is set to 0 (the default value), the statistics are
                               persisted forever.
        ==================     ====================================================================


        :return:
            A JSON message indicating success.

        """
        params = {
            "f" : "json",
            "maxHistory" : max_history,
            "enabled" : enabled,
            "samplingInterval" : interval
        }
        url = self._url + "/settings/edit"
        return self._con.post(path=url,
                              postdata=params)
    #----------------------------------------------------------------------
    def create(self,
               reportname,
               queries,
               metadata=None,
               since="LAST_DAY",
               from_value=None,
               to_value=None,
               aggregation_interval=None):
        """
        Creates a new usage report. A usage report is created by submitting
        a JSON representation of the usage report to this operation.

        ====================     ====================================================================
        **Argument**             **Description**
        --------------------     --------------------------------------------------------------------
        reportname               Required string. The unique name of the report.
        --------------------     --------------------------------------------------------------------
        queries                  Required string. A list of queries for which to generate the report.
                                 Specify the list as an array of JSON objects representing the queries.
                                 Each query specifies the list of metrics to be queried for a given
                                 set of resourceURIs.

                                 The queries parameter has the following sub-parameters:

                                 - resourceURIs -- Comma-separated list of resource URIs for which
                                   to report metrics. This specifies the services or folders for
                                   which to gather metrics. The resourceURI is formatted as below:
                                    - services/ -- Entire Site
                                    - services/Folder/ -- Folder within a Site. Reports metrics
                                      aggregated across all services within that Folder and Sub-Folders.
                                    - services/Folder/ServiceName.ServiceType -- Service in a
                                      specified folder, for example:
                                         - services/Map_bv_999.MapServer
                                         - services/ServiceName.ServiceType
                                    - Service in the root folder, for example: Map_bv_999.MapServer.
        --------------------     --------------------------------------------------------------------
        metadata                 Optional string. Any JSON object representing presentation tier
                                 data for the usage report, such as report title, colors,
                                 line-styles, etc. Also used to denote visibility in ArcGIS Server
                                 Manager for reports created with the Administrator Directory. To
                                 make any report created in the Administrator Directory visible to
                                 Manager, include *"managerReport":true* in the metadata JSON object.
                                 When this value is not set (default), reports are not visible in
                                 Manager. This behavior can be extended to any client that wants to
                                 interact with the Administrator Directory. Any user-created value
                                 will need to be processed by the client.
        --------------------     --------------------------------------------------------------------
        since                    Optional string. The time duration of the report. The supported
                                 values are: LAST_DAY, LAST_WEEK, LAST_MONTH, LAST_YEAR, CUSTOM

                                 - LAST_DAY represents a time range spanning the previous 24 hours.
                                   This is the default value.
                                 - LAST_WEEK represents a time range spanning the previous 7 days.
                                 - LAST_MONTH represents a time range spanning the previous 30 days.
                                 - LAST_YEAR represents a time range spanning the previous 365 days.
                                 - CUSTOM represents a time range that is specified using the from
                                   and to parameters.
        --------------------     --------------------------------------------------------------------
        from_value               Optional string. Only valid when *since* is CUSTOM. The timestamp
                                 (milliseconds since UNIX epoch, namely January 1, 1970, 00:00:00 GMT)
                                 for the beginning period of the report.
        --------------------     --------------------------------------------------------------------
        to_value                 Optional string. Only valid when *since* is CUSTOM. The timestamp
                                 (milliseconds since UNIX epoch, namely January 1, 1970, 00:00:00 GMT)
                                 for the ending period of the report.
        --------------------     --------------------------------------------------------------------
        aggregation_interval     Optional string. The aggregation interval in minutes. Server metrics
                                 are aggregated and returned for time slices aggregated using the
                                 specified aggregation interval. The time range for the report,
                                 specified using the *since* parameter (and *from_value* and
                                 *to_value* when since is CUSTOM) is split into multiple slices, each
                                 covering an aggregation interval. Server metrics are then aggregated
                                 for each time slice and returned as data points in the report data.
                                 When the aggregation_interval is not specified, the following defaults
                                 are used:

                                   - LAST_DAY: 30 minutes
                                   - LAST_WEEK: 4 hours
                                   - LAST_MONTH: 24 hours
                                   - LAST_YEAR: 1 week
                                   - CUSTOM: 30 minutes up to 1 day, 4 hours up to 1 week, 1
                                   day up to 30 days, and 1 week for longer periods.

                                 If the interval specified in Usage Reports Settings is more than
                                 the aggregationInterval, the interval is used instead.
        ====================     ====================================================================


        :return:
            A JSON indicating success.


        .. code-block:: python

            USAGE EXAMPLE:

            >>> queryObj = [{
                "resourceURIs": ["services/Map_bv_999.MapServer"],
                "metrics": ["RequestCount"]
                }]
            >>> obj.createReport(
                reportname="SampleReport",
                queries=queryObj,
                metadata="This could be any String or JSON Object.",
                since="LAST_DAY"
                )
        """
        url = self._url + "/add"
        temp = False
        params = {

            "reportname" : reportname,
            "since" : since,
        }
        if  not metadata:
            params['metadata'] = {
                "temp" : temp,
                "title" : reportname,
                "managerReport" : False,

            }
        else:
            params['metadata'] = metadata
        if isinstance(queries, dict):
            params["queries"] = [queries]
        elif isinstance(queries, list):
            params["queries"] = queries
        if aggregation_interval:
            params['aggregationInterval'] = aggregation_interval
        if since.lower() == "custom":
            params['to'] = to_value
            params['from'] = from_value
        p = {"f" : "json",'usagereport' : params}
        res = self._con.post(path=url,
                             postdata=p)
        #  Refresh the metrics object
        self._init()
        for report in self.list():
            if report.reportname.lower() == reportname.lower():
                return report
        return res
    #----------------------------------------------------------------------
    def quick_report(self,
                     since="LAST_WEEK",
                     queries="services/",
                     metrics="RequestsFailed"):
        """
        The operation quick_report generates an on the fly usage report for
        a service, services, or folder.

        ====================     ====================================================================
        **Argument**             **Description**
        --------------------     --------------------------------------------------------------------
        since                    Optional string. The time duration of the report. The supported
                                 values are: LAST_DAY, LAST_WEEK, LAST_MONTH, LAST_YEAR, CUSTOM

                                 - LAST_DAY represents a time range spanning the previous 24 hours.
                                   This is the default value.
                                 - LAST_WEEK represents a time range spanning the previous 7 days.
                                 - LAST_MONTH represents a time range spanning the previous 30 days.
                                 - LAST_YEAR represents a time range spanning the previous 365 days.
                                 - CUSTOM represents a time range that is specified using the from
                                   and to parameters.
        --------------------     --------------------------------------------------------------------
        queries                  Required string. A list of queries for which to generate the report.
                                 Specify the list as an array of JSON objects representing the queries.
                                 Each query specifies the list of metrics to be queried for a given
                                 set of resourceURIs.

                                 The queries parameter has the following sub-parameters:

                                 - resourceURIs -- Comma-separated list of resource URIs for which
                                   to report metrics. This specifies the services or folders for
                                   which to gather metrics. The resourceURI is formatted as below:
                                    - services/ -- Entire Site
                                    - services/Folder/ -- Folder within a Site. Reports metrics
                                      aggregated across all services within that Folder and Sub-Folders.
                                    - services/Folder/ServiceName.ServiceType -- Service in a
                                      specified folder, for example:
                                         - services/Map_bv_999.MapServer
                                         - services/ServiceName.ServiceType
                                    - Service in the root folder, for example: Map_bv_999.MapServer.
        --------------------     --------------------------------------------------------------------
        metrics                  Optional string. Comma separated list of metrics to be reported.

                                 Supported metrics are:

                                    - RequestCount -- the number of requests received
                                    - RequestsFailed -- the number of requests that failed
                                    - RequestsTimedOut -- the number of requests that timed out
                                    - RequestMaxResponseTime -- the maximum response time
                                    - RequestAvgResponseTime -- the average response time
                                    - ServiceActiveInstances -- the maximum number of active
                                      (running) service instances sampled at 1 minute intervals,
                                      for a specified service
        ====================     ====================================================================


        :return:
            A Python dictionary of data on a successful query.

        """
        from uuid import uuid4
        queries = {
            "resourceURIs": queries.split(','),
            "metrics" : metrics.split(',')
        }
        reportname = uuid4().hex
        metadata = {
                "temp" : True,
                "title" : reportname,
                "managerReport" : False,

            }
        res = self.create(reportname=reportname,
                                       queries=queries,
                                       since=since,
                                       metadata=metadata)
        if isinstance(res, Report):
            data = res.query()
            res.delete()
            return data
        return res
########################################################################
class Report(BaseServer):
    """
    **(This class should not be created by a user)**

    A utility class representing a single usage report returned by ArcGIS Server.

    A Usage Report is used to obtain ArcGIS Server usage data for specified
    resources during a given time period. It specifies the parameters for
    obtaining server usage data, time range (parameters since, from_value, to_value),
    aggregation interval, and queries (which specify the metrics to be
    gathered for a collection of server resources, such as folders and
    services).
    """
    _con = None
    _url = None
    _json = None
    _reportname = None
    _since = None
    _from = None
    _to = None
    _aggregationInterval = None
    _queries = None
    _metadata = None
    #----------------------------------------------------------------------
    def __init__(self, url, gis,
                 initialize=False):
        """
        Constructor

        ==================     ====================================================================
        **Argument**           **Description**
        ------------------     --------------------------------------------------------------------
        url                    Required string. The machine URL.
        ------------------     --------------------------------------------------------------------
        gis                    Optional string. The GIS or Server object.
        ------------------     --------------------------------------------------------------------
        initialize             Optional string. Denotes whether to load the machine properties at
                               creation (True). Default is False.
        ==================     ====================================================================

        """
        super(Report, self).__init__(url=url, gis=gis)
        self._con = gis
        self._url = url
        if initialize:
            self._init()
    #----------------------------------------------------------------------
    def edit(self):
        """
        Edits the usage report. To edit a usage report, submit
        the complete JSON representation of the usage report which
        includes updates to the usage report properties. The name of the
        report cannot be changed when editing the usage report.

        Values are changed in the class, to edit a property like
        metrics, pass in a new value.

        :return:
            A JSON indicating success.

        """

        usagereport_dict = {
            "reportname": self._reportname,
            "queries": self._queries,
            "since": self._since,
            "metadata": self._metadata,
            "to" : self._to,
            "from" : self._from,
            "aggregationInterval" : self._aggregationInterval
        }
        params = {
            "f" : "json",
            "usagereport" : json.dumps(usagereport_dict)
        }
        url = self._url + "/edit"
        return self._con.post(path=url,
                              postdata=params)
    #----------------------------------------------------------------------
    def delete(self):
        """
        Deletes this usage report.

        :return:
            A JSON indicating success.
        """
        url = self._url + "/delete"
        params = {
            "f" : "json",
        }
        return self._con.post(path=url,
                              postdata=params)
    #----------------------------------------------------------------------
    def query(self, query_filter=None):
        """
        Retrieves server usage data for this report. This operation
        aggregates and filters server usage statistics for the entire
        ArcGIS Server site. The report data is aggregated in a time slice,
        which is obtained by dividing up the time duration by the default
        (or specified) aggregationInterval parameter in the report. Each
        time slice is represented by a timestamp, which represents the
        ending period of that time slice.

        In the JSON response, the queried data is returned for each metric-
        resource URI combination in a query. In the report-data section,
        the queried data is represented as an array of numerical values. A
        response of null indicates that data is not available or requests
        were not logged for that metric in the corresponding time-slice.

        ==================     ====================================================================
        **Argument**           **Description**
        ------------------     --------------------------------------------------------------------
        query_filter           Optional string. The report data can be filtered by the machine
                               where the data is generated. The filter accepts a comma-separated
                               list of machine names; * represents all machines.
        ==================     ====================================================================


        :return:
            A JSON containing the server usage data.


        .. code-block:: python

            USAGE EXAMPLE 1: Filters for the specified machines

            {"machines": ["WIN-85VQ4T2LR5N", "WIN-239486728937"]}

        .. code-block:: python

            USAGE EXAMPLE 2: No filtering, all machines are accepted

            {"machines": "*"}

        """
        if query_filter is None:
            query_filter = {"machines": "*"}
        params = {
            "f" : "json",
            "filter" : query_filter,
            "filterType" : 'json'
        }
        url = self._url + "/data"
        return self._con.get(path=url, params=params)
