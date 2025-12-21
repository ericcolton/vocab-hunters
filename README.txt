


python3 -m venv venv
source venv/bin/activate

# install
pip install flask gunicorn python-dotenv

# flask run
flask --app app run --debug

# script run
./Scripts/phase2.py < ~/OneDrive/Development/homework-hero/Requests/wings_of_fire.json | ./Scripts/phase5.py > ~/OneDrive/Development/homework-hero/Output/section_11_wof_4.pdf
