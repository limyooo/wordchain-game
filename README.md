# 🔗 WordChain

An AI-powered English word chain game. You and an AI partner take turns: each word must start with the last letter of the previous word. See how long you can keep the chain going!

> **Example:** apple → **e**gg → **g**arden → **n**est → **t**iger …

Built as a cloud computing course project: a FastAPI backend with Claude as the AI player, planned for serverless deployment on AWS with Terraform.

## Features

- **Rule checks in code:** every word (yours and the AI's) must
  - contain letters only and be at least 3 letters long
  - start with the last letter of the previous word
  - be a real English word (checked against a dictionary)
  - not repeat a word already used in the game
- **Real AI partner:** Claude picks the next word; if it breaks a rule, the backend rejects it and asks again (up to 3 tries)
- **Random opening word** for every new game
- **Saved games:** game history is stored, so you can return to any game

## Tech stack

| Layer | Technology |
| --- | --- |
| Backend | Python, FastAPI, Uvicorn |
| AI | Claude API (`claude-haiku-5-5`) via the `anthropic` SDK |
| Frontend | HTML, CSS, vanilla JavaScript |
| Dictionary | [dwyl/english-words](https://github.com/dwyl/english-words) (`words_alpha.txt`) |
| Storage | Local JSON files (moving to DynamoDB) |

## Run locally

**Requirements:** Python 3.11+ and a Claude API key from the [Claude Console](https://platform.claude.com).

```bash
# 1. Clone the repo
git clone https://github.com/limyooo/wordchain.git
cd wordchain

# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Set your API key (never commit it)
export ANTHROPIC_API_KEY="your-key-here"

# 4. Start the server
python main.py
```

Open http://localhost:8000 and click **New game**.

The dictionary file `words.txt` is included in the repo. If it is missing, download it with:

```bash
curl -o words.txt https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt
```

## API

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/sessions` | List games, newest first |
| `POST` | `/api/sessions` | Start a new game with a random opening word |
| `DELETE` | `/api/sessions/{id}` | Delete a game |
| `GET` | `/api/sessions/{id}/messages` | Get a game's messages |
| `POST` | `/api/sessions/{id}/chat` | Send a word, get the AI's reply. Body: `{"message": "egg"}` |

Interactive API docs are available at http://localhost:8000/docs.

## Project structure

```
wordchain/
├── main.py            # FastAPI app: game rules, AI, storage, endpoints
├── words.txt          # English dictionary (one word per line)
├── requirements.txt   # Python dependencies
├── static/
│   ├── index.html     # Game page
│   ├── app.js         # Frontend logic
│   └── style.css      # Styles
└── sessions/          # Saved games (local only, not committed)
```

## Roadmap

**Proof of concept**
- [x] Playable game with rule checks and a real AI partner
- [ ] Package the backend for AWS Lambda
- [ ] Provision infrastructure with Terraform
- [ ] Store the API key in AWS Secrets Manager

**Minimum viable product**
- [ ] Move game storage from local files to DynamoDB
- [ ] Serve the frontend from S3 + CloudFront
- [ ] CI/CD with GitHub Actions
- [ ] CloudWatch alarms and LLM cost tracking
- [ ] Request throttling in API Gateway

**Stretch goals**
- [ ] Hints and word definitions
- [ ] Vocabulary list of new words after each game
- [ ] Difficulty levels

## Security

The Claude API key is read from the `ANTHROPIC_API_KEY` environment variable and is never stored in the code. `.gitignore` excludes `.env`, `.idea/`, `.venv/` and `sessions/`.