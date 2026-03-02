
## Final_Alert (4 csvs per day)
alert_labels = ',pfaf_id,name,name_1,CentroidX,CentroidY,Admin1_count,Admin1_names,area_km2,rfr_score,cfr_score,Alert_level,Days_until_peak,GloFAS_2yr,GloFAS_5yr,GloFAS_20yr,Alert_Score,PeakArrivalScore,TwoYScore,FiveYScore,TwtyYScore,Sum_Score_x,GFMS_TotalArea_km,GFMS_perc_Area,GFMS_MeanDepth,GFMS_MaxDepth,GFMS_Duration,GFMS_area_score,GFMS_perc_area_score,MeanD_Score,MaxD_Score,Duration_Score,Sum_Score_y,MOM_Score,Hazard_Score,Scaled_Riverine_Risk,Scaled_Coastal_Risk,Flag,1-Day_TotalArea_km2,1-Day_perc_Area,1-Day_CS_TotalArea_km2,1-Day_CS_perc_Area,2-Day_TotalArea_km2,2-Day_perc_Area,3-Day_TotalArea_km2,3-Day_perc_Area,DFO_area_1day_score,DFO_percarea_1day_score,DFO_area_2day_score,DFO_percarea_2day_score,DFO_area_3day_score,DFO_percarea_3day_score,DFOTotal_Score,onedayFlood_Area_km,onedayperc_Area,fivedayFlood_Area_km,fivedayperc_Area,VIIRS_area_1day_score,VIIRS_percarea_1day_score,VIIRS_area_5day_score,VIIRS_percarea_5day_score,VIIRSTotal_Score,Severity,Alert,Status'.split(",")

## GLOFAS (1 csv, 1 geojson per day)
glofas_labels ='Point No,Station,Basin,Country,Lat,Lon,Upstream area,Forecast Date,max_EPS,GloFAS_2yr,GloFAS_5yr,GloFAS_20yr,Alert_level,Days_until_peak,pfaf_id'.split(",")

## VIIRS
# image (2 tiffs per day: 1day, 5day)
# summary (1 per day)
viirs_sum_labels = 'pfaf_id,onedayFlood_Area_km,onedayperc_Area,fivedayFlood_Area_km,fivedayperc_Area'.split(",")

## GFMS
# image (1-4 tiffs per day (inconsistent): Flood_byStore)
# summary (8 csvs per day, every 3h)
gfms_sum_labels = 'pfaf_id,GFMS_TotalArea_km,GFMS_perc_Area,GFMS_MeanDepth,GFMS_MaxDepth,GFMS_Duration'.split(",")

## DFO
# image (1 tiff per day (inconsistent, some days are missing): Flood_3-Day_250m)
# summary (1 csv per day)
gfo_sum_labels = ',pfaf_id,1-Day_TotalArea_km2,1-Day_perc_Area,1-Day_CS_TotalArea_km2,1-Day_CS_perc_Area,2-Day_TotalArea_km2,2-Day_perc_Area,3-Day_TotalArea_km2,3-Day_perc_Area'.split(",")

## HWRF
# summary (1-4 csvs per day, inconsistent)
hwrf_sum_labels = 'pfaf_id,Rain_TotalArea_km,perc_Area,MeanRain,MaxRain'.split(",")

