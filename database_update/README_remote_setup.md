
wget https://raw.githubusercontent.com/KatKatKateryna/MoMProduction/db_structure/first_setup/setup.sh

chmod +x setup.sh

./setup.sh

# now the main or dev branch is downloaded

cd ~/MoMProduction

git fetch origin
git switch -c <branch> origin/<branch>
