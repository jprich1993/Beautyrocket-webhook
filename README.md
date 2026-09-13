# Beautyrocket Instagram Webhook

Python/Flask webhook for the Beautyrocket Instagram account.

## Purpose

When a person sends a direct message to the Instagram account for the first time, the bot sends the initial greeting. After that, the bot records the Instagram user ID and does not automatically respond to additional messages.

## Local setup

1. Create and activate the Python virtual environment.
2. Install dependencies:

   pip install -r requirements.txt

3. Create a .env file with the required environment variables.
4. Start the webhook:

   python instagram_api.py

## Webhook endpoints

- GET / — health check
- GET /webhook — Meta webhook verification
- POST /webhook — receives Instagram messaging events

## Security

Secrets are stored in .env and are excluded from Git through .gitignore.

The local SQLite database greeted_users.db is also excluded from Git.

## Deployment

The application is designed to run with Gunicorn on a production hosting service such as Render.

## Privacy Policy

The project's privacy policy is available in privacy-policy.html.
