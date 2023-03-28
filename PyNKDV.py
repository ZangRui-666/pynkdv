import math
from scipy import stats
import nkdv
import osmnx as ox
import numpy as np
import pandas as pd
from io import StringIO
from qgis.core import *  # It's required by QgsApplication.setPrefixPath("/Applications/QGIS.app/Contents/MacOS", True)
import sys
import networkx as nx
from shapely.geometry import Point
from shapely.geometry import LineString
import os
import geopandas as gpd
import processing
from processing.core.Processing import Processing

"""
for windows
from qgis.core import *
from qgis.core import QgsProcessing
from qgis.core import QgsProcessingAlgorithm
from qgis.core import QgsProcessingMultiStepFeedback
from qgis.core import QgsProcessingParameterRasterDestination
import sys
# Supply path to qgis install location
QgsApplication.setPrefixPath("D:\qgis", True)
# import processing
#
# # Create a reference to the QgsApplication.  Setting the
# # second argument to False disables the GUI.
qgs = QgsApplication([], False)
#
# # Load providers
qgs.initQgis()
# Write your code here to load some layers, use processing
# algorithms, etc.
sys.path.append(r"D:\qgis\apps\qgis-ltr\python\plugins")
import processing
qgs.exitQgis()
"""


def setPath(qgs_path, processing_path):
    QgsApplication.setPrefixPath(qgs_path, True)
    qgs = QgsApplication([], False)
    qgs.initQgis()
    for i in processing_path:
        sys.path.append(i)
    #


def process_edges(graph):
    edge_list = []
    for i, edge in enumerate(graph.edges):
        if i % 10000 == 0:
            print("current edge: ", i)
        node1_id = edge[0]
        node2_id = edge[1]
        length = graph[node1_id][node2_id][0]['length']
        edge_list.append([node1_id, node2_id, length])
    return pd.DataFrame(edge_list, columns=['u_id', 'v_id', 'length'])


def project_data_points_and_generate_layer(graph, nodes):
    """
    Parameters:
    @nodes: data nodes
    @graph: projected, consolidated road network
    """
    longitudes = nodes[:, 0]
    latitudes = nodes[:, 1]
    points_list = [Point((lon, lat)) for lon, lat in zip(longitudes, latitudes)]  # turn into shapely geometry
    points = gpd.GeoSeries(points_list,
                           crs='epsg:4326')  # turn into GeoSeries, with default crs, i.e., ox.settings.default_crs
    points.to_file('results/points_layer.gpkg')
    points_proj = points.to_crs(graph.graph['crs'])

    xs = [pp.x for pp in points_proj]
    ys = [pp.y for pp in points_proj]
    # find the nearest edges for all query points
    nearest_edges = ox.nearest_edges(graph, xs, ys)  # dist = 1 for meters
    print("finished finding all the nearest edges")

    distances = []

    # find distances
    for i in range(len(longitudes)):
        if i % 10000 == 0:
            print("current point: ", i)

        # edge's 2 ends's node id
        point1_id = nearest_edges[i][0]  # the nearest edge's first end's node id
        point2_id = nearest_edges[i][1]  # the nearest edge's second end's node id

        # generate projection on nearest edge
        ref_point = Point(xs[i], ys[i])  # the projected point

        edge = LineString(
            [(graph.nodes[point1_id]['x'], graph.nodes[point1_id]['y']),
             (graph.nodes[point2_id]['x'], graph.nodes[point2_id]['y'])])
        # this is the distance from one end to the projected position (but we don't know the other end)
        projected_dist = edge.project(ref_point)
        distances.append([point1_id, point2_id, projected_dist])

    distances_df = pd.DataFrame(distances, columns=['u_id', 'v_id', 'distance'])
    distances_df = distances_df.sort_values(by=['u_id', 'v_id', 'distance'], ascending=[True, True, True],
                                            ignore_index=True)

    return distances_df


def fix_direction(graph):
    x_dic = {}
    for i, node in enumerate(graph.nodes(data=True)):
        x_dic[i] = node[1]['x']
    for i, edge in enumerate(graph.edges(data=True)):
        shapely_geometry = edge[2]['geometry']
        x, y = shapely_geometry.xy
        if abs(x[0] - x_dic[edge[0]]) > 0.00001:  # edge0 is u (source ID)
            edge[2]['geometry'] = shapely_geometry.reverse()


def merge(edges_df, dis_df, nodes_num):
    print('start merge')
    # df1 is edge dataframe and df2 is distance dataframe
    merge_df = pd.merge(edges_df, dis_df, on=['u_id', 'v_id'], how='left')
    merge_df = merge_df.sort_values(by=['u_id', 'v_id'], ascending=[True, True])
    merge_df = merge_df.reset_index()
    pd.DataFrame([[nodes_num, edges_df.shape[0]]]).to_csv('../../res_df_csv', sep=' ', index=False, header=False)
    merge_df.to_csv('test_merge_df_left_join', sep=' ')

    merge_np = merge_df.to_numpy()
    if np.isnan(merge_np[0][4]):  # or we can use merge_np[0][4]>0
        row = [merge_np[0][1], merge_np[0][2], merge_np[0][3], 0]
    else:
        row = [merge_np[0][1], merge_np[0][2], merge_np[0][3], 1, merge_np[0][4]]
    res = []
    for i in range(1, merge_np.shape[0]):
        if merge_np[i][1] == merge_np[i - 1][1] and merge_np[i][2] == merge_np[i - 1][2]:
            row[3] = row[3] + 1
            row.append(merge_np[i][4])
        elif np.isnan(merge_np[i][4]):
            res.append(row)
            row = [merge_np[i][1], merge_np[i][2], merge_np[i][3], 0]
        else:
            res.append(row)
            row = [merge_np[i][1], merge_np[i][2], merge_np[i][3], 1, merge_np[i][4]]
    res.append(row)
    with open('test_write_file2', 'w') as fp:
        fp.write("%s " % str(nodes_num))
        fp.write("%s\n" % str(edges_df.shape[0]))
        for list_in in res:
            fp.write("%s " % str(int(list_in[0])))
            fp.write("%s" % str(int(list_in[1])))
            for i in range(2, len(list_in)):
                # write each item on a new line
                fp.write(" %s" % str(list_in[i]))
            fp.write("\n")
        fp.close()
        print('Done')


def buildGraphFromPoints(path_from):
    nodes = np.genfromtxt(path_from, delimiter=' ')
    longitudes = nodes[:, 0]
    latitudes = nodes[:, 1]

    points_list = [Point((lng, lat)) for lat, lng in zip(latitudes, longitudes)]  # turn into shapely geometry
    points = gpd.GeoSeries(points_list,
                           crs='epsg:4326')  # turn into geoseries, with default crs, i.e., ox.settings.default_crs
    points.to_file('results/points_layer.gpkg')


def add_kd_value(gdf, value_se, to_file):
    columns_list = gdf.columns.tolist()
    columns_list.append('value')
    gdf = gdf.reindex(columns=columns_list)
    print('gdf.info', gdf.info())
    print('data.info', value_se.info())
    gdf['value'] = value_se
    print(gdf.info())
    print(to_file)
    gdf.to_file(to_file)
    # gdf.plot()


def update_length(df1, df2):
    print(df1.info())
    print(df2.info())
    df1['length'] = df2['length']


def map_road_network(location_data):
    # qgs = QgsApplication([], False)
    # qgs.initQgis()
    Processing.initialize()

    data_arr = np.genfromtxt(location_data, delimiter=' ', max_rows=100)
    data_df = pd.DataFrame(data_arr, columns=['lon', 'lat'])
    # data cleaning
    data_df = data_df[(np.abs(stats.zscore(data_df)) < 3).all(axis=1)]
    lat_max = data_df['lat'].max()
    lat_min = data_df['lat'].min()
    lon_max = data_df['lon'].max()
    lon_min = data_df['lon'].min()
    print(lat_max, lat_min, lon_max, lon_min)
    g1 = ox.graph_from_bbox(lat_max, lat_min, lon_max, lon_min, simplify=True, network_type='drive')
    gc1 = ox.consolidate_intersections(ox.project_graph(g1), tolerance=20, rebuild_graph=True)
    undi_gc1 = gc1.to_undirected()
    single_undi_gc1 = nx.Graph(undi_gc1)
    g = nx.MultiGraph(single_undi_gc1)
    nodes_num = g.number_of_nodes()
    fix_direction(g)
    print('2')
    edge_df = process_edges(g)
    geo_path_1 = 'results/.gpkg/' + 'geo1.gpkg'
    ox.save_graph_geopackage(g, geo_path_1)
    df1 = gpd.read_file(geo_path_1, layer='edges')
    geo_path_2 = 'results/simplified_gpkg/' + 'simplified' + '.gpkg'
    df1 = df1[['geometry']]
    df1.to_file(geo_path_2, driver='GPKG', layer='edges')

    added_geometry_filename = '/Users/patrick/Desktop/qgis/add_geometry_.shp'
    processing.run("qgis:exportaddgeometrycolumns",
                   {'INPUT': geo_path_2 + '|layername=edges', 'CALC_METHOD': 0, 'OUTPUT': added_geometry_filename})

    df2 = gpd.read_file(added_geometry_filename)
    update_length(edge_df, df2)

    distance_df = project_data_points_and_generate_layer(g, data_arr)
    merge(edge_df, distance_df, nodes_num)
    road_data = [geo_path_2, 'test_write_file']
    return road_data


def output(results, output_file_name):
    df4 = gpd.read_file(results[1])
    df5 = pd.read_csv(results[0], sep=',', skiprows=1, names=['a', 'b', 'c', 'd', 'value'])['value']
    add_kd_value(df4, df5, output_file_name)
    print('aoligei')


class PyNKDV:
    def __init__(self, road_data, bandwidth=1000, lixel_size=5, num_threads=8):
        self.graph_path = road_data[0]
        self.data_file = road_data[1]
        self.bandwidth = bandwidth
        self.lixel_size = lixel_size
        self.num_threads = num_threads

    def compute(self):
        Processing.initialize()
        qgis_split_output = '/Users/patrick/Desktop/qgis/split_by_qgis/split.shp'
        processing.run("native:splitlinesbylength", {
            'INPUT': self.graph_path + '|layername=edges',
            'LENGTH': self.lixel_size, 'OUTPUT': qgis_split_output})

        example = nkdv.NKDV(bandwidth=self.bandwidth, lixel_reg_length=self.lixel_size, method=3)
        example.set_data(self.data_file)
        example.compute()
        result_io = StringIO(example.result)
        df_cplusplus = pd.read_csv(result_io, sep=' ', skiprows=1, names=['a', 'b', 'c', 'value'])
        c_output_path = 'c++_output_'
        df_cplusplus.to_csv(c_output_path)
        result = [c_output_path, qgis_split_output]
        return result
        # add_kd_value(df4, series_cplusplus, final_output_path)
