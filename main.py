from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import smtplib
from dotenv import load_dotenv
from requests_oauthlib import OAuth2Session
from flask import Flask, request, redirect, render_template, flash, session, url_for
import logging
import csv

# Load environment variables from .env file
load_dotenv()

# Flask application
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Add a secret key for session management

# Configure logging
logging.basicConfig(level=logging.INFO)


def get_zoom_credentials(account):
    if account == 'PFS':
        return {
            'client_id': os.getenv('ZOOM_CLIENT_ID_PFS'),
            'client_secret': os.getenv('ZOOM_CLIENT_SECRET_PFS'),
            'auth_url': os.getenv('ZOOM_AUTHORIZATION_BASE_URL_PFS'),
        }
    elif account == 'JFS':
        return {
            'client_id': os.getenv('ZOOM_CLIENT_ID_JFS'),
            'client_secret': os.getenv('ZOOM_CLIENT_SECRET_JFS'),
            'auth_url': os.getenv('ZOOM_AUTHORIZATION_BASE_URL_JFS'),
        }
    elif account == 'WebDev':
        return {
            'client_id': os.getenv('ZOOM_CLIENT_ID_WEBDEV'),
            'client_secret': os.getenv('ZOOM_CLIENT_SECRET_WEBDEV'),
            'auth_url': os.getenv('ZOOM_AUTHORIZATION_BASE_URL_WEBDEV'),
        }
    else:
        raise ValueError("Invalid Zoom account selected")


@app.route('/')
def home():
    return render_template('select_account.html')


@app.route('/start_auth', methods=['POST'])
def start_auth():
    zoom_account = request.form['zoom_account']
    session['zoom_account'] = zoom_account

    credentials = get_zoom_credentials(zoom_account)
    zoom = OAuth2Session(
        credentials['client_id'], redirect_uri=os.getenv('ZOOM_REDIRECT_URI'))

    authorization_url, _ = zoom.authorization_url(credentials['auth_url'])
    return redirect(authorization_url)


@app.route('/zoom/oauthredirect')
def zoom_oauthredirect():
    zoom_account = session.get('zoom_account')
    credentials = get_zoom_credentials(zoom_account)

    zoom = OAuth2Session(
        credentials['client_id'], redirect_uri=os.getenv('ZOOM_REDIRECT_URI'))

    try:
        zoom.fetch_token(
            'https://zoom.us/oauth/token',
            authorization_response=request.url,
            client_secret=credentials['client_secret']
        )
        session['token'] = zoom.token  # Save the token in the session
    except Exception as e:
        logging.error(f"Error fetching OAuth token: {e}")
        flash("Error fetching OAuth token.")
        return render_template('error.html', error_message="Failed to authenticate with Zoom.")

    # After successful authentication, redirect to the input_dates page
    return redirect(url_for('input_dates'))


@app.route('/input_dates')
def input_dates():
    return render_template('input_dates.html')


def get_webinar_participants(zoom, webinar_id, start_date, end_date):
    url = f'https://api.zoom.us/v2/report/webinars/{webinar_id}/participants'
    params = {
        'from': start_date,
        'to': end_date,
    }
    response = zoom.get(url, params=params)

    if response.status_code != 200:
        logging.error(f"Error fetching participants: {
                      response.status_code} - {response.text}")
        return None

    return response.json()


@app.route('/fetch_participants', methods=['POST'])
def fetch_participants():
    try:
        zoom_account = session.get('zoom_account')
        webinar_id = request.form['webinar_id']
        start_date = request.form['start_date']
        end_date = request.form['end_date']

        session['webinar_id'] = webinar_id
        session['start_date'] = start_date
        session['end_date'] = end_date

        credentials = get_zoom_credentials(zoom_account)
        zoom = OAuth2Session(
            credentials['client_id'], token=session.get('token'))

        participants = get_webinar_participants(
            zoom, webinar_id, start_date, end_date)
        if not participants or 'participants' not in participants:
            raise ValueError("Error fetching participants.")

        try:
            # Create directory structure
            base_dir = f"C:/FLM Attendance Tracker/{
                zoom_account} Zoom/{webinar_id}/{start_date}"
            os.makedirs(base_dir, exist_ok=True)

            # Save the participants to a CSV file called attended.csv
            attendance_file = os.path.join(base_dir, 'attended.csv')
            with open(attendance_file, 'w', newline='') as csvfile:
                fieldnames = ['user_email']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for participant in participants['participants']:
                    writer.writerow(
                        {'user_email': participant.get('user_email', '')})

            # Read the attended participants from attendance.csv
            with open(attendance_file, 'r') as csvfile:
                reader = csv.DictReader(csvfile)
                attended = [row['user_email'] for row in reader]

            # Read the registrants from registrants.csv
            with open('registrants.csv', 'r') as csvfile:
                reader = csv.DictReader(csvfile)
                registrants = [row['user_email'] for row in reader]

            # Find registrants who did not attend
            not_attended = [
                email for email in registrants if email not in attended]

            # Save the non-attendees to a CSV file called nonattended.csv
            nonattendance_file = os.path.join(base_dir, 'nonattended.csv')
            with open(nonattendance_file, 'w', newline='') as csvfile:
                fieldnames = ['user_email']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for email in not_attended:
                    writer.writerow({'user_email': email})

        except IOError as e:
            raise IOError(f"Error saving participants to CSV: {e}")

        return render_template('success.html', message="attended.csv and nonattended.csv have been saved successfully.")

    except Exception as e:
        logging.error(f"Error during process: {e}")
        return redirect(url_for('error', error_message=str(e)))


@app.route('/error')
def error():
    error_message = request.args.get(
        'error_message', 'An unknown error occurred.')
    return render_template('error.html', error_message=error_message)


@app.route('/send_email')
def send_email():
    return render_template('send_email.html')


@app.route('/process_email', methods=['POST'])
def process_email():
    try:
        email_message = request.form['email_message']
        subject = request.form['subject']

        # Retrieve session variables
        zoom_account = session.get('zoom_account')
        webinar_id = session.get('webinar_id')
        start_date = session.get('start_date')

        if not zoom_account or not webinar_id or not start_date:
            raise ValueError(
                "Missing session information. Please start the process again.")

        # Construct the path to the nonattended.csv file
        base_dir = f"C:/FLM Attendance Tracker/{
            zoom_account} Zoom/{webinar_id}/{start_date}"
        nonattendance_file = os.path.join(base_dir, 'nonattended.csv')

        # Read email addresses from nonattended.csv
        email_addresses = []
        with open(nonattendance_file, 'r') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                email_addresses.append(row['user_email'])

        # Email server configuration
        smtp_server = 'smtp.gmail.com'
        smtp_port = 587
        sender_email = 'flmtechteam.pfs@gmail.com'
        sender_password = 'kmjhhjythmnemfhp'

        # Set up the SMTP server
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()  # Enable security
        server.login(sender_email, sender_password)

        # Send an email to each address
        for recipient_email in email_addresses:
            msg = MIMEMultipart()
            msg['From'] = sender_email
            msg['To'] = recipient_email
            msg['Subject'] = subject

            # Attach the message from the text area
            msg.attach(MIMEText(email_message, 'plain'))

            # Send the email
            server.send_message(msg)
            del msg  # Delete the message object to avoid memory issues

        # Close the SMTP server
        server.quit()

        return render_template('success.html', message="Emails sent successfully to all not attended participants.")

    except Exception as e:
        logging.error(f"Error sending email: {e}")
        return render_template('error.html', error_message="Failed to send the email.")


if __name__ == '__main__':
    app.run(host='localhost', port=44328, ssl_context='adhoc')
