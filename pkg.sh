pip install virtualenv
virtualenv venv
./venv/bin/pip install -r ./requirements.txt
./venv/bin/pip install pyinstaller
./venv/bin/pyinstaller -F ./main.py --hiddenimport pkg_resources.py2_warn
rm -rf ./venv
