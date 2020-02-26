import json
import time
import traceback

import numpy as np
import pandas as pd
import pdal
from geopandas import GeoDataFrame, sjoin
from hdbscan import HDBSCAN
from scipy.spatial.qhull import ConvexHull
from shapely import geometry
from shapely.geometry import Point
from shapely.wkt import loads
from sklearn.cluster import KMeans

from object_detection.helper_functions import \
    find_n_clusters_peaks, \
    df_to_pg, \
    former_preprocess_now_add_pid, \
    ept_reader


class DetectorTree:
    '''

    '''

    def __init__(self, box):
        """
        Initialize the tree class holding all the points
        :param box: a tuple of (xmin, xmax, ymin, ymax)

        """
        # initialize the boundary box in 3 ways
        self.xmin, self.ymin, self.xmax, self.ymax = box
        self.geom_box = geometry.box(self.xmin, self.ymin, self.xmax, self.ymax)
        self.wkt_box = self.geom_box.wkt

        # read points from the ept
        start = time.time()
        self.raw_points = ept_reader(self.wkt_box)
        end = time.time()
        print(f'reading from the ept took {round(end - start, 2)}')
        print(f'Total amount of points read: {self.raw_points.shape[0]}')

        # Masks
        self.ground_mask = self.raw_points['Classification'] == 2
        self.n_returns_mask = self.raw_points['NumberOfReturns'] < 2

        masks = np.vstack([self.ground_mask, self.n_returns_mask])
        self.masks = np.sum(masks, axis=0) == 0

        # initialize an empty DataFrames for writing to database later
        self.tree_coords = pd.DataFrame(data={'X': [],
                                              'Y': [],
                                              'Z': []
                                              })
        self.tree_df = GeoDataFrame({'xy_clusterID': [],
                                     'geometry': [],
                                     "meanZ": [],
                                     "n_pts": [],
                                     "NX": [],
                                     "NY": [],
                                     "NZ": [],
                                     "Coplanar": []})
        # for easy visualiziation
        df_to_pg(GeoDataFrame(
            data={'what': 'boundingbox',
                  'geometry': self.geom_box},
            index=[0]),
            schema='bomen',
            table_name='bbox')

    def hdbscan_on_points(self, min_cluster_size, min_samples, xyz=False):
        """
        Performs hdbscan on input points.
        :param min_cluster_size: [int], minimum cluster size
        :param min_samples: : [int], min samples
        >> see https://hdbscan.readthedocs.io/en/latest/parameter_selection.html

        :param xyz: [bool] if True the clustering will be done over xyz otherwise xy.

        :return: writes the points assigned with clusters to self.clustered_points
        """

        masked_points = self.raw_points[self.masks]
        start = time.time()
        if xyz:
            xy = np.array([masked_points['X'], masked_points['Y'], masked_points['Z']]).T
        else:
            xy = np.array([masked_points['X'], masked_points['Y']]).T

        xy_clusterer = HDBSCAN(min_cluster_size=min_cluster_size,
                               min_samples=min_samples)
        xy_clusterer.fit(xy)

        clustered_points = pd.DataFrame({'X': masked_points['X'],
                                         'Y': masked_points['Y'],
                                         'Z': masked_points['Z'],
                                         'Red': masked_points['Red'],
                                         'Green': masked_points['Green'],
                                         'Blue': masked_points['Blue'],
                                         'HAG': masked_points['HAG1'],
                                         'Coplanar': masked_points['Coplanar'],
                                         'NormalX': masked_points['NormalX'],
                                         'NormalY': masked_points['NormalY'],
                                         'NormalZ': masked_points['NormalZ'],
                                         'Classification': xy_clusterer.labels_})
        # remove "noise" points
        self.clustered_points = clustered_points[clustered_points.Classification >= 0]
        end = time.time()
        print(f'found {np.unique(len(np.unique(self.clustered_points.Classification)))[0]} xy_clusters')
        print(f'clustering on xy took {round(end - start, 2)} seconds')

    def convex_hullify(self, points, kmean_pols=False):
        """
        Makes a 2d convex hull around around a set of points.

        :param points: [pd.DataFrame] The points to hullify around
        :param kmean_pols: [bool] switch for chosing between the kmean polygons hullifier or not

        :return: writes to polygons in self.tree_df (initialized in init)
        """

        # empties the tree.df if it exists
        try:
            self.tree_df.drop(self.tree_df.index, inplace=True)
        except:
            pass

        for name, group in points.groupby('Classification'):
            if group.shape[0] <= 3:
                # remove polygons that contain too little points to hullify around
                points.drop(points.groupby('Classification').get_group(name).index)
            else:
                # performs convexhull
                coords = np.array([group.X, group.Y]).T
                # :TODO can I do this better? Params?
                polygon = ConvexHull(coords)

                # build wkt string
                wkt = 'POLYGON (('
                for group_id in polygon.vertices:
                    x, y = polygon.points[group_id]
                    wkt += f'{x} {y},'
                # close the polygon
                firstx, firsty = polygon.points[polygon.vertices[0]]
                wkt = wkt + f'{firstx} {firsty}))'

                # if there are less than 8 points per square meter; it's not a tree
                if (group.shape[0] / loads(wkt).area) <= 3:
                    print(f'dropped {name} because less than 3 pts/m2')
                    points.drop(points.groupby('Classification').get_group(name).index)

                # if the area is larger than 800 m2; it's not a tree
                elif kmean_pols and loads(wkt).area >= 800:
                    print(f'dropped {name} because polygon is too big')
                    points.drop(points.groupby('Classification').get_group(name).index)

                # here goes more selection of polygons
                # :TODO put all the elifs and the write to df in own function?
                # :TODO if NZ > 0.7, likely to contain low points

                else:
                    # write to df
                    self.tree_df.loc[len(self.tree_df)] = [int(name),
                                                           loads(wkt),
                                                           group.Z.mean(),
                                                           group.shape[0],
                                                           group.NormalX.mean(),
                                                           group.NormalY.mean(),
                                                           group.NormalZ.mean(),
                                                           group.Coplanar.mean()]

    def find_points_in_polygons(self, polygon_df):
        """
        For the kmean clustering, more points is better.
        Therefor the returnmask is not used.
        This function finds all the raw points within a polygon.

        :param polygon_df: [pd.DataFrame] DataFrame with polygons

        :return: writes to self.xy_grouped_points
        """
        # remove ground points
        cluster_points = self.raw_points[self.ground_mask.__invert__()]
        # cluster_points = self.raw_points.copy()

        # do i need to pre-process?
        cluster_data = former_preprocess_now_add_pid(
            cluster_points[['X', 'Y', 'Z',
                            'Red', 'Green', 'Blue',
                            'Intensity', 'ReturnNumber', 'NumberOfReturns',
                            'HAG1', "Coplanar", "NormalX", "NormalY",
                            "NormalZ"]])
        xy = [Point(coords) for coords in zip(cluster_points['X'], cluster_points['Y'], cluster_points['Z'])]
        points_df = GeoDataFrame(cluster_data, geometry=xy)

        # find raw points without ground within the polygons.
        grouped_points = sjoin(points_df, polygon_df, how='left')
        grouped_points['X'] = grouped_points.geometry.apply(lambda p: p.x)
        grouped_points['Y'] = grouped_points.geometry.apply(lambda p: p.y)

        # TODO no idea where the nans are coming from
        # dirty hack, hope not to many important points go missing
        print(f'removing {np.isnan(grouped_points.index_right).sum()} mystery nans <- merge_points_polygons')
        grouped_points = grouped_points[~np.isnan(grouped_points.index_right)]

        # remove noise
        print(f'Removed {np.array([grouped_points.xy_clusterID < 0]).sum()} noise points')
        grouped_points = grouped_points[grouped_points.xy_clusterID >= 0]
        self.xy_grouped_points = grouped_points.rename(columns={'index_right': 'polygon_id'})

    def find_peaks(self, grouped_points, min_dist, relative_threshold, round_val):
        for name, group in grouped_points.groupby('xy_clusterID'):
            tree_area = float(self.tree_df.loc[self.tree_df['xy_clusterID'] == int(name)].geometry.area)

            # A tree needs to be bigger than 2 meters2 and have more than 10 points
            if name >= 0 and tree_area >= 2 and group.shape[0] >= 10:
                group = group.drop(['geometry'], axis=1)
                n_clusters, coordinates = find_n_clusters_peaks(cluster_data=group
                                                                # is rounded to a multiple of the gridsize
                                                                , min_dist=min_dist
                                                                , round_val=round_val
                                                                , relative_threshold=relative_threshold
                                                                )


    def kmean_cluster(self, xy_grouped_points, min_dist, relative_threshold, round_val):
        """

        :param xy_grouped_points: [GeoPandasDataFrame]: the points classified per polygon
        :param min_dist: [int]: see find_n_clusters_peaks in self.kmean_cluster_group
        :param relative_threshold: [int]: see find_n_clusters_peaks in self.kmean_cluster_group
        :param round_val: [int]: see find_n_clusters_peaks in self.kmean_cluster_group

        :return: writes to self.kmean_grouped_points
        """
        # TODO: see if it is possible to use initial clusterpoints
        # Initialize dataframes to write to
        assert type(min_dist) == int, 'min_dist is is in pixels, therefor must be a int'
        to_cluster = pd.DataFrame(data={'pid': []})
        labs = pd.DataFrame(data={'labs': [0] * len(xy_grouped_points.pid),
                                  'pid': xy_grouped_points.pid,
                                  'HeightAboveGround': [0] * len(xy_grouped_points.pid),
                                  'Coplanar': [0] * len(xy_grouped_points.pid),
                                  'NX': [0] * len(xy_grouped_points.pid),
                                  'NY': [0] * len(xy_grouped_points.pid),
                                  'NZ': [0] * len(xy_grouped_points.pid)},

                            index=xy_grouped_points.pid)
        self.kmean_grouped_points = xy_grouped_points.copy()

        for name, group in self.kmean_grouped_points.groupby('xy_clusterID'):
            tree_area = float(self.tree_df.loc[self.tree_df['xy_clusterID'] == int(name)].geometry.area)
            # to ensure no deep copy/copy stuff
            try:
                del new_labs
            except Exception as E:
                pass

            # A tree needs to be bigger than 2 meters2 and have more than 10 points
            if name >= 0 and tree_area >= 2 and group.shape[0] >= 10:
                group = group.drop(['geometry'], axis=1)
                # run through second pdal filter
                # :TODO under construction
                to_cluster = self.second_filter(group.to_records())
                # actual kmeans clustering.
                kmeans_labels = self.kmean_cluster_group(group=to_cluster,
                                                         min_dist=min_dist,
                                                         relative_threshold=relative_threshold,
                                                         round_val=round_val)

                # Create the new labs dataframe
                new_labs = pd.DataFrame(data={'labs': kmeans_labels,
                                              'pid': to_cluster.pid,
                                              'HeightAboveGround': to_cluster.HAG1,
                                              'Coplanar': to_cluster.Coplanar,
                                              'NormalX': to_cluster.NormalX,
                                              'NormalY': to_cluster.NormalY,
                                              'NormalZ': to_cluster.NormalZ},
                                        index=to_cluster.pid)

                # add the new labels to the labels dataframe
                try:
                    labs.update(new_labs)
                except ValueError as e:
                    print(f'Fatal: {e}')
                    raise

                print(
                    f"polygon: {int(name)}  \t "
                    f"area:  {round(tree_area, 2)} \t "
                    f"Found {len(np.unique(kmeans_labels))} clusters"
                )

            else:
                # :TODO if tree too small or not enough points, drop the points instead of adding ids
                new_labs = pd.DataFrame(data={'labs': [-1] * len(group.pid),
                                              'pid': to_cluster.pid},
                                        index=group.pid)
                labs.update(new_labs)
        self.kmean_grouped_points['value_clusterID'] = labs.labs * 10

        # Add columns for adding to the polygons later
        added_cols = ['HeightAboveGround', 'Coplanar', 'NX', 'NY', 'NZ', "Coplanar"]
        for col in added_cols:
            self.kmean_grouped_points[col] = eval(f'labs.{col}')

        # factorize the cluster labels
        # :TODO do something so all -1 values are removed
        combi_ids = ["".join(row) for row in
                     self.kmean_grouped_points[['value_clusterID', 'xy_clusterID']].values.astype(str)]
        self.kmean_grouped_points['Classification'] = pd.factorize(combi_ids)[0]

    def kmean_cluster_group(self, group, min_dist, relative_threshold, round_val):
        """
        Kmeans clustering performed on a subset (tree or cluster of trees) of points


        :param group: [pd.DataFrame]The points including x y and z columns
        :param min_dist: see find_n_clusters_peaks
        :param relative_threshold: see find_n_clusters_peaks
        :param round_val: see find_n_clusters_peaks
        :return: a series of the labels in the order of the input DataFrame
        """
        # Clustering is performed on only x y and z
        cluster_data = np.array([group.X,
                                 group.Y,
                                 group.Z]).T

        # Number of clusters is found, necessary for kmeans
        # :TODO allometric scaling????
        n_clusters, coordinates = find_n_clusters_peaks(cluster_data=cluster_data
                                                        # is rounded to a multiple of the gridsize
                                                        , min_dist=min_dist
                                                        , round_val=round_val
                                                        , relative_threshold=relative_threshold
                                                        )

        # actual kmeans clustering
        # :TODO init{‘k-means++’, ‘random’} or ndarray of shape (n_clusters, n_features), default=’k-means++’

        # to add initial cluster points
        if len(coordinates) > 0:
            # if False:
            coordinates = np.array(coordinates)
            self.tree_coords = self.tree_coords.append(pd.DataFrame(data={'X': coordinates.T[0],
                                                                          'Y': coordinates.T[1],
                                                                          'Z': coordinates.T[2]}))
            self.tree_coords['geometry'] = [Point(x, y) for x, y, z in zip(self.tree_coords.X,
                                                                           self.tree_coords.Y,
                                                                           self.tree_coords.Z)]
            self.tree_coords = GeoDataFrame(self.tree_coords, geometry='geometry')

            # TODO nearest neighbours?
            print('max iter is 1')
            kmeans = KMeans(n_clusters=n_clusters, max_iter=100, init=np.array(coordinates)).fit(cluster_data)
        else:
            kmeans = KMeans(n_clusters=n_clusters, max_iter=1).fit(cluster_data)
        return kmeans.labels_

    def second_filter(self, points):
        """
        a second pdal filter. At the moment it is not used...

        :param points: points on which to perform the filter
        :return: [pd.DataFrame] of the filtered points.
        """
        pipeline_config = {
            "pipeline": [
                {
                    "type": "filters.range",
                    "limits": "HAG1[0.5:)"
                },
                {
                    "type": "filters.normal",
                    "knn": 8
                },
                {
                    "type": "filters.approximatecoplanar",
                    "knn": 8,
                    "thresh1": 25,
                    "thresh2": 6
                }
            ]
        }

        try:
            p = pdal.Pipeline(json.dumps(pipeline_config), arrays=[points])
            p.validate()  # check if our JSON and options were good
            p.execute()
            # normalZ dissapears?
            arrays = p.arrays
            out_points = arrays[0]

        except Exception as e:
            trace = traceback.format_exc()
            print(f'{points.shape[0]} points, probably not enough to second filter.')
            print("Unexpected error:", trace)

            out_points = points.copy()
        return pd.DataFrame(out_points)
