# RRB ALP CBT-1 Practice App

A lightweight, installable web application to help candidates prepare for the Railway Recruitment Board Assistant Loco Pilot (RRB ALP) CBT-1 examination.

**Live Website:** https://rrb-alp.onrender.com  
**GitHub Repository:** https://github.com/9ariz1/rrb-alp

## Features

- **Topic-wise Study:** Mathematics, Reasoning, Physics, Chemistry, Biology and General Awareness.
- **MCQ Practice:** Practise questions and review your answers.
- **Previous Papers:** Access questions extracted from RRB ALP CBT-1 papers.
- **Important Questions:** Review important and repeated topics.
- **Wrong Questions:** Revisit incorrectly answered questions.
- **Student Accounts:** Registration and login.
- **Progress Tracking:** Track practice progress where supported.
- **Progressive Web App (PWA):** Install the website through Chrome and open it like an app.

## Technology Stack

- **Frontend:** HTML5, CSS3, JavaScript
- **Backend:** Python, Flask
- **Database:** SQLite
- **Deployment:** Render
- **Automation:** GitHub Actions

## Install the App

1. Open https://rrb-alp.onrender.com in Google Chrome.
2. Open the browser menu.
3. Select **Install RRB ALP CBT-1 Practice**.
4. Confirm the installation.
5. Launch the app from your desktop or app launcher.

## Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/9ariz1/rrb-alp.git
cd rrb-alp
```

### 2. Create a virtual environment

**Windows:**

```bash
python -m venv venv
venv\Scripts\activate
```

**Linux/macOS:**

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a local `.env` file containing the environment variables required by the application. Never commit API keys, passwords or other secrets to GitHub.

### 5. Start the application

```bash
python app.py
```

Open http://127.0.0.1:5000 in your browser.

## PWA and Offline Status

The website has been installed successfully as a PWA, and MCQs work while connected to the internet.

**Offline MCQs are still under development.** At the latest test, MCQs did not work after disconnecting the internet and reopening the installed app. Offline functionality must be tested again after the service worker and question-data caching are updated.

## GitHub Actions — Render Keep-Alive

The repository contains the workflow:

`.github/workflows/keep-alive.yml`

It sends a request to the Render login page using GitHub Actions. The workflow was manually tested and completed successfully.

The configured schedule is every five minutes. GitHub may delay scheduled workflows, so this does not guarantee that a free Render service will always remain awake.

## Project Structure

```text
rrb-alp/
├── app.py
├── templates/
├── static/
│   ├── css/
│   ├── js/
│   └── sw.js
├── data/
├── .github/
│   └── workflows/
│       └── keep-alive.yml
├── requirements.txt
└── README.md
```

Actual paths may vary as the project evolves.

## Current Development Priorities

- Improve offline MCQ functionality.
- Cache required question data and static assets for offline use.
- Test PWA behaviour with the internet disconnected.
- Continue improving the study and practice experience.

## Disclaimer

This is an independent educational project for RRB ALP CBT-1 preparation. It is not affiliated with or endorsed by the Railway Recruitment Board. Refer to official RRB notifications for authoritative examination information.

## Author

**Mo Ariz**

- GitHub: https://github.com/9ariz1
- Portfolio: https://ariz-portfolio.onrender.com
- LinkedIn: https://www.linkedin.com/in/mo-ariz/
