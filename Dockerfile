FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# a container takes its timezone from TZ (setup.sh sets it with timedatectl)
ENV TZ=UTC

COPY first_setup/ /tmp/first_setup/

# setup.sh as on a server, minus what a container cannot do: sudo (the build
# runs as root), timedatectl and the nginx systemd service (no systemd)
RUN sed -i 's/\r//' /tmp/first_setup/setup.sh \
    && sed -i -e 's/sudo //g' -e '/timedatectl/d' -e '/install_nginx_mom_prod\.sh/d' /tmp/first_setup/setup.sh \
    && bash /tmp/first_setup/setup.sh

# run python of the conda env setup.sh creates (myenv), in the repository it
# clones into ($HOME/MoMProduction); activation sets the GDAL/PROJ variables
ENTRYPOINT ["bash", "-c", "source \"$HOME/miniconda3/etc/profile.d/conda.sh\" && conda activate myenv && cd \"$HOME/MoMProduction\" && exec python \"$@\"", "python"]
