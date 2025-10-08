# Speaker Diarization Project

This project provides real-time speaker diarization capabilities using Google Cloud Speech-to-Text API with a React frontend and FastAPI backend.

## Features

- **Real-time Audio Processing**: Live audio streaming with WebSocket connection
- **Speaker Diarization**: Automatic speaker identification and separation
- **Voice Activity Detection (VAD)**: Optional audio gating for better performance
- **Modern UI**: React-based frontend with real-time transcript display
- **Configurable Parameters**: Environment variables for fine-tuning

## Project Structure

```
speaker-diarization/
├── backend/                 # FastAPI backend server
│   ├── main.py             # Main application file
│   ├── requirements.txt    # Python dependencies
│   └── diarvenv/          # Python virtual environment
├── frontend/               # React frontend application
│   ├── src/               # Source code
│   ├── public/            # Public assets
│   └── package.json       # Node.js dependencies
└── README.md              # This file
```

## Prerequisites

### Backend Requirements
- Python 3.12+
- Google Cloud Speech-to-Text API credentials
- Virtual environment support

### Frontend Requirements
- Node.js 16+
- npm or yarn package manager

## Setup Instructions

### 1. Backend Setup

#### Install Python Dependencies
```bash
cd backend

# Create and activate virtual environment (if not already created)
python -m venv diarvenv
source diarvenv/bin/activate  # On Windows: diarvenv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

#### Google Cloud Setup
1. Create a Google Cloud Project and enable the Speech-to-Text API
2. Create a service account and download the JSON key file
3. Set the environment variable:
```bash
export GOOGLE_APPLICATION_CREDENTIALS="path/to/your/service-account-key.json"
```

#### Optional: Environment Configuration
You can customize the diarization behavior with these environment variables:

```bash
# Voice Activity Detection
export VAD_ENABLED=1                    # Enable/disable VAD (0 or 1)
export VAD_AGGRESSIVENESS=2             # VAD sensitivity (0-3)
export VAD_PREROLL_MS=150               # Pre-roll buffer in milliseconds
export VAD_HANGOVER_MS=400              # Hangover time in milliseconds

# Speaker Voting
export VOTE_TAIL_WORDS=5                # Number of tail words to weight
export VOTE_TAIL_WEIGHT=2               # Weight multiplier for tail words

# Keepalive
export SILENCE_KEEPALIVE_MS=250         # Keepalive interval during silence
```

### 2. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install
```

## Running the Application

### Start the Backend Server

```bash
cd backend
source diarvenv/bin/activate  # Activate virtual environment
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The backend will be available at: `http://localhost:8000`

### Start the Frontend Development Server

```bash
cd frontend
npm start
```

The frontend will be available at: `http://localhost:3000`

## Usage

1. Open your browser and navigate to `http://localhost:3000`
2. Allow microphone permissions when prompted
3. Start speaking - the application will:
   - Display real-time transcripts
   - Identify different speakers
   - Show speaker tags for each segment

