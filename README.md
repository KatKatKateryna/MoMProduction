# ModelofModels Production
The following guide is tested on Ubuntu 18.04 and 20.04 LTS.  
The current testing VM is a m1.small instance on [Jetstream cloud](https://portal.xsede.org/jetstream) with 2 vcpus, 4GB memory, 20GB storage with an extra 100GB volume attached.   

## 0. Timezone setup
Upstream data are produced by agencies across the world, UTC time zone is recommended.  
In Ubuntu, set timezone to UTC: 
```
sudo timedatectl set-timezone UTC
```
And run "timedatectl" to check: 
```
                      Local time: Mon 2022-04-04 01:45:14 UTC
                  Universal time: Mon 2022-04-04 01:45:14 UTC
                        RTC time: Mon 2022-04-04 01:45:14
                       Time zone: UTC (UTC, +0000)
       System clock synchronized: yes
systemd-timesyncd.service active: yes
                 RTC in local TZ: no
```
## 1. Setup Python environment
All installation steps are packaged into Linux and Windows Powershell installers:
```
bash ./setup.sh
```
or
```
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
```
This includes Python installation/update, repo cloning, conda installation and custom venv creation and activation. 

## 2. Initialize the setup
Please copy [sample_production.cfg](https://github.com/Global-Flood-Assessment/MoMProduction/blob/main/sample_production.cfg) to **production.cfg**, or run initialize.py, it will do the copy.  
Check production.cfg: 
- in general section, change WORKING_DIR (base directory for downloading and processing data) and PRODUCT_DIR (base directory for the data products) if necessary;
- in glofas section, fill in user/passwd for ftp site;  
- in dfo section, fill in token for download [more on Automating NRT Downloads](https://nrt4.modaps.eosdis.nasa.gov/archive/allData/61)  
```
[general]
WORKING_DIR: ~/MoM/Processing
PRODUCT_DIR: ~/MoM/Products

[glofas]
HOST: data-portal.ecmwf.int
USER: ???
PASSWD: ???
DIRECTORY: /for_PDC

[dfo]
TOKEN: ???
```
Initialize the production setup: 
```
python initialize.py
```
It performs the following tasks:   
- create the folder structures defined in production.cfg
- check username/password, token in production.cfg
- unzip watershed.shp 

## 3. Cron jobs
`setup_crontab.sh` installs this folder's jobs on the schedule in `settings.CRON_SCHEDULE` (UTC). Re-running it replaces only this folder's entries.
```
./setup_crontab.sh
crontab -l
```
Every job runs through `run_mom_job.sh`: it activates the conda environment, runs one job at a time per folder (`logs/mom_job.lock`), writes the job's output to `logs/<job>.log`, and publishes the alerts when `PUBLISH_CSV` / `PUBLISH_TILES` are on.

On a new installation no job runs before its scheduled time, so make the last days first:
```
./run_mom_job.sh -j BACKFILL --start <YYYYMMDD, two days ago>
```

## 3.1 Docker
```
cp sample_production.cfg production.cfg     # fill in the credentials and the data folders
./docker_up.sh                              # build, start, install the cron schedule
docker compose exec mom ./run_mom_job.sh -j BACKFILL --start <YYYYMMDD, two days ago>
```
Where everything is on the host:
- **Data:** exactly the `WORKING_DIR` and `PRODUCT_DIR` of `production.cfg`, with `~` being the home of the user who runs `docker_up.sh` (`~/MoM/Products` is `/home/<user>/MoM/Products`). `docker_up.sh` mounts them into the container (`~` there is `/root`) through `docker-compose.override.yml`.
- **Logs and the job lock:** `./docker_run/logs` (cron job output) and `./docker_run/backfill_logs`.
- **Configuration:** `./production.cfg`, read-only. For the alert push, uncomment the `.env` line in `docker-compose.yml`.

Nothing is kept only inside the container, so removing or rebuilding it loses no data. Files are written as root. The container doesn't start cron if `production.cfg` is incomplete or the data folders aren't mounted; `docker compose logs mom` shows the action to take.
```
docker compose exec mom crontab -l
docker compose exec mom ./run_mom_job.sh -j HAFS
docker compose down
```

## 4. Storage requirements 
The minimum required free disk space for data processing is 20G. 

Daily processing jobs generate less than 3.0Gb data, includes both the downloaded data and products.  

Processing folder holds the downloaded data and intermediate outputs, all the contents under processing can be deleted periodically to save the disk space. The processed data is usually archived daily as a zip, such as gfms_20211202.zip in its corresponding folder. For DFO/VIIRS data, setting storage save flag to False in production.cfg deletes the downloaded data immediately after the processing, it saves a little more than 2.0Gb (DFO: 2Gb, VIIRS: around 200Mb). 
``` 
production.cfg:

[storage]
dfo_save: False 
viirs_save: False 
```
### 4.1 Free up disk space
If the disk space is low, use the following steps to free up disk space:
* Remove the zip files in sub-folders under Processing, the zip files contain the downloaded data that already been processed, it is safe to delete them periodically.
* If more space needed, under Products, all the tiffs images in _image subfolder can be removed, they are only for the archive purpose.  
* A good practice is to delete the older *.zip/*.tiff first, and keep at least the recent data up to two weeks. 

Disk space can be monitored with the optional Monitor Module. 
## 5. Folder structures 
Folder structures is defined in production.cfg, the default one is listed. Modify [general],[processing_dir], [products_dir] to change the locations. 
```
MoM
├── Processing
│   ├── dfo
│   ├── gfms
│   ├── glofas
│   ├── hwrf
│   ├── logs
│   └── viirs
└── Products
    ├── DFO
    │   ├── DFO_image
    │   ├── DFO_MoM
    │   └── DFO_summary
    ├── Final_Alert
    ├── GFMS
    │   ├── GFMS_image
    │   ├── GFMS_MoM
    │   └── GFMS_summary
    ├── GLOFAS
    ├── HWRF
    │   ├── HWRF_image
    │   ├── HWRF_MoM
    │   └── HWRF_summary
    └── VIIRS
        ├── VIIRS_image
        ├── VIIRS_MoM
        └── VIIRS_summary
```

## 6. Processing modules & data

```
settings.py
initialize.py
processing
├── MoM_run.py
├── utilities.py
├── watershed_cells.py
├── proj_env.py
├── publish
│   ├── publish_utils.py
│   ├── publish_alerts.py
│   └── publish_tiles.py
├── gfms
│   ├── GFMS_tool.py
│   └── GFMS_MoM.py
├── hwrf
│   ├── HWRF_tool.py
│   └── HWRF_MoM.py
├── hafs
│   └── HAFS_tool.py
├── dfo
│   ├── DFO_tool.py
│   └── DFO_MoM.py
└── viirs
    ├── VIIRS_tool.py
    └── VIIRS_MoM.py
```
Run the pipeline modules from the repository root, e.g. `python -m processing.hafs.HAFS_tool`
(running them by file path fails, because they import `settings` from the root and `processing` as a package).
The data required is in data folder:
```
data
├── Admin0_1_union_centroid.csv
├── Attributes.csv
├── DFO_Weightage.csv
├── GFMS_Weightage.csv
├── HWRF_Weightage.csv
├── Resilience_Index.csv
└── VIIRS_Weightage.csv
```

## 7. Profiling scripts

1. Start logging disk usage before running the script (separate shell, linux)
```
while true; do du -sb MoM >> disk_log_GFMS.txt; sleep 1; done
```
```
while true; do du -sb MoM >> disk_log_VIIRS.txt; sleep 1; done
```
```
while true; do du -sb MoM >> disk_log_DFO.txt; sleep 1; done
```
```
while true; do du -sb MoM >> disk_log_HWRF.txt; sleep 1; done
```

2. If parallel processing applied (e.g. in VIIRS), track live RAM usage (separate shell, linux). 
(Need to add 'print("Main PID:", os.getpid())' to the start on the script, if not there yet)
```
PID=YourPID
while true; do
    total_kb=$(ps --no-headers -o rss --ppid $PID | awk '{sum+=$1} END {print sum+0}')
    parent_kb=$(ps -o rss= -p $PID)
    echo $((total_kb + parent_kb)) >> ram_log_VIIRS.txt
    sleep 1
done
```

3. Total time, CPU, RAM, per-function profiling (run from the MoMProduction dir, with activated venv).
For Linux conda setup:
```
pip install py-spy
```
```
/usr/bin/time -v py-spy record -r 50 --subprocesses -o profile_GFMS.json --format speedscope -- python -m processing.MoM_run -j GFMS 2> resources_GFMS.txt
```
```
/usr/bin/time -v py-spy record -r 50 --subprocesses -o profile_VIIRS.json --format speedscope -- python -m processing.MoM_run -j VIIRS 2> resources_VIIRS.txt
```
```
/usr/bin/time -v py-spy record -r 50 --subprocesses -o profile_DFO.json --format speedscope -- python -m processing.MoM_run -j DFO 2> resources_DFO.txt
```
```
/usr/bin/time -v py-spy record -r 50 --subprocesses -o profile_HWRF.json --format speedscope -- python -m processing.MoM_run -j HWRF 2> resources_HWRF.txt
```

For Windows uv setup:
```
uv add py-spy
python -m processing.MoM_run -j VIIRS
```
Run this from Admin Powershell (per-function profiling):
```
py-spy record 50 --subprocesses -o profile_VIIRS.json --format speedscope --pid <PID>
```
Launch this in PowerShell (get Timestamp, RAM, CPU) - does NOT include subprocesses:
```
typeperf "\Process(python)\Working Set" "\Process(python)\% Processor Time" -si 1 -o resources_VIIRS_win.txt
```

4. After it finished, find peak and minimum memory (during and before running the code). The difference is the temp storage needed. 
For Linux:
```
awk 'NR==1{min=$1; max=$1} {if($1<min) min=$1; if($1>max) max=$1} END{print (max-min)/1024/1024/1024 " GB"}' disk_log_VIIRS.txt
```
For RAM live tracking (if applicable) find maximum:
```
sort -n ram_log_VIIRS.txt | tail -1 | awk '{print $1/1024/1024 " Gb"}'
```

For Windows (Powershell), prints max RAM and CPU:
```
"{0} GB" -f [math]::Round(
    (Get-Content resources_VIIRS_win.txt |
     Select-Object -Skip 2 |
     ForEach-Object {
         $cols = $_ -replace '"' -split ','
         [double]$cols[1]
     } |
     Measure-Object -Maximum).Maximum / 1GB,
3)
```


___________________________________________________________________________

## New installation notes

**Linux** (Ubuntu-24.04):
Create setup.sh file at the root folder with
```
nano setup.sh
```
and paste the content of first_setup/setup.sh file from this repo. Ctrl+O (Save), Ctrl+C (Exit). Replace branch name to "main" for stable release. Run the script:
```
. first_setup/setup.sh
```
Create persistent env variables for this machine's User scope (can manually replace in 'production.cfg', but not recommended):
```
echo 'export AUTH_GLOFAS_USER=myvalue' >> ~/.bashrc
echo 'export AUTH_GLOFAS_PASSWD=myvalue' >> ~/.bashrc
echo 'export AUTH_DFO_TOKEN=myvalue' >> ~/.bashrc
```

**Windows** (tested on CI, but not on a new Windows machine):
Create setup.ps1 file at the root folder and paste the content of first_setup/setup.ps1 file from this repo. Replace branch name to "main" for stable release. Run from Admin Powershell:
```
Set-ExecutionPolicy Bypass -Scope Process -Force
```
```
. .\first_setup\setup.ps1
```
Create persistent env variables for this machine's User scope (can manually replace in 'production.cfg', but not recommended):
```
[System.Environment]::SetEnvironmentVariable("AUTH_GLOFAS_USER", "myvalue", "User")
[System.Environment]::SetEnvironmentVariable("AUTH_GLOFAS_PASSWD", "myvalue", "User")
[System.Environment]::SetEnvironmentVariable("AUTH_DFO_TOKEN", "myvalue", "User")
```