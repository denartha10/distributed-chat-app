run:
	npx tailwindcss -i templates/css/app.css -o static/css/styles.css
	@uvicorn main:app --reload --host 0.0.0.0

css:
	npx tailwindcss -i templates/css/app.css -o static/css/styles.css

production:
	nohup uvicorn main:app --host 0.0.0.0 --port 4321 &
	sudo service nginx restart
