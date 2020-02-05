from object_detection.tree_detector import Detector_tree

tree = Detector_tree((122564.2,490367.9, 122581.3,490388.9))
tree.cluster_on_xy(40, 60)
tree.convex_hullify()
print(tree.tree_df)

'''
POLYGON ((122628.09 490371.28,122631.57 490345.01,122632.23 490344.87,122632.88 490344.74,122633.86 490344.55,122637.11 490344.5,122657.8 490344.54,122667.86 490345.16,122668.04 490345.43,122668.11 490345.78,122668.15 490371.61,122667.22 490371.83,122639.79 490373.39,122637.57 490373.39,122633.97 490373.3,122628.09 490371.28))
POLYGON ((122637.28 490344.8,122625.97 490349.84,122620.0 490350.28,122619.69 490350.24,122619.62 490345.76,122620.14 490344.84,122620.51 490344.71,122621.81 490344.51,122624.95999999999 490344.54,122630.55 490344.6,122637.28 490344.8))
POLYGON ((122658.05 490367.12,122660.15 490367.37,122660.92 490367.82,122665.07 490370.54,122664.61 490371.2,122655.93 490373.36,122651.93 490373.21,122650.29 490373.11,122655.36 490367.7,122655.68 490367.36,122656.33 490367.23,122658.05 490367.12))
POLYGON ((122629.65 490368.19,122627.2 490364.97,122627.29 490363.79,122627.47 490362.47,122628.48 490360.23,122640.65 490351.84,122645.41 490349.56,122649.41 490348.39,122656.62 490347.74,122659.63 490348.32,122659.66 490352.99,122654.24 490360.42,122636.04 490370.26,122634.97 490370.74,122629.65 490368.19))
POLYGON ((122663.95999999999 490349.05,122660.29 490346.95,122659.70999999999 490345.73,122659.56 490345.29,122659.79 490344.6,122660.1 490344.54,122661.59 490344.52,122663.37 490344.52,122665.6 490344.54,122666.66 490344.66,122667.43 490344.76,122667.70999999999 490345.39,122667.84 490346.72,122666.34 490348.87,122663.95999999999 490349.05))
'''