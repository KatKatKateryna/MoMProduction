# MoMProduction in a container: the conda environment, the five cron jobs and
# cron itself, so the container runs the production schedule unattended.
#
# The repository is copied from the build context, not cloned, so the image is
# built from the checkout in hand. setup.sh is not used: it installs system
# packages with sudo, sets the timezone with timedatectl and resets the
# checkout to its own remote, none of which apply to an image build.
FROM continuumio/miniconda3:latest

# a container takes its timezone from TZ (on a server setup.sh sets it with
# timedatectl); all job schedules and file dates are UTC
ENV TZ=UTC
ENV DEBIAN_FRONTEND=noninteractive

RUN ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime && echo "$TZ" > /etc/timezone \
    && apt-get update \
    && apt-get install -y --no-install-recommends cron wget \
    && rm -rf /var/lib/apt/lists/*

# the repository lives outside ~, where production.cfg's ~/... data folders
# are mounted (docker_up.sh); run_mom_job.sh finds conda in /opt/conda when
# cron's PATH has no conda
WORKDIR /opt/MoMProduction

# the environment first, so it is only rebuilt when environment.yml changes
COPY environment.yml ./
RUN conda config --add channels conda-forge \
    && conda config --set channel_priority strict \
    && conda env create -n momenv -f environment.yml \
    && conda install -n momenv --override-channels -c conda-forge libgdal-hdf4=3.12.2 --no-update-deps -y \
    && conda clean -afy

COPY . ./
# the job lock and the cron jobs' output (logs/) and the backfill logs
# (backfill/logs/) go to /var/lib/mom, a volume of their own
RUN chmod +x run_mom_job.sh setup_crontab.sh docker-entrypoint.sh \
    && rm -rf logs backfill/logs \
    && ln -s /var/lib/mom/logs logs \
    && ln -s /var/lib/mom/backfill_logs backfill/logs

# logs and the lock outlive the container (docker-compose.yml mounts
# docker_run here); the data folders are mounted by docker_up.sh
VOLUME ["/var/lib/mom"]

ENTRYPOINT ["./docker-entrypoint.sh"]
