
## For the scripts and development:
wget https://raw.githubusercontent.com/KatKatKateryna/MoMProduction/db_structure/first_setup/setup_dev.sh

chmod +x setup_dev.sh

./setup_dev.sh

# now the main or dev branch is downloaded

cd ~/MoMProduction
git switch db_structure
git config --global credential.helper store

conda activate myenv

git status
git add <file>
git restore --staged first_setup/db_setup/db_config.cfg
git commit
git push
git pull

## For the docker image apps (e.g. DO container registry):
docker build -t mom_local_repo:latest .
docker tag mom_local_repo:latest registry.digitalocean.com/mom-container-registry/mom_local_repo:latest
docker login registry.digitalocean.com

docker push registry.digitalocean.com/mom-container-registry/mom_local_repo:latest
