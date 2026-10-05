# AI Teaching Assistant

Turn your study material into a personal tutor. Upload a PDF or Word file,
ask questions about it, and test yourself with quizzes made from it.

- **Chat with your document.** Answers come from your file and show the page
  they came from, so you can check them.
- **Quiz yourself.** Pick a topic and difficulty, get multiple-choice or
  short-answer questions, and see your score with the right answers.
- **Search the web (optional).** Add extra context from the internet, always
  shown separately from what your document says.
- **Free to run.** Uses only free tiers of online services.

> Runs on your own computer for now. Online hosting and Google login will come later.

## Getting started

### 1. What you need

- **Python 3.12**
- Free accounts and API keys for:
  - **Google AI Studio** (the AI model): aistudio.google.com/apikey
  - **Pinecone** (stores your document for searching): pinecone.io
  - **MongoDB Atlas** (stores your chats and quizzes): cloud.mongodb.com
  - **OpenRouter** (optional backup AI when Google is busy): openrouter.ai/keys

Step-by-step instructions for each account (including the Pinecone index
settings) are in [docs/setup-and-deployment.md](docs/setup-and-deployment.md).

### 2. Install (one time)

```bash
cd backend  && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && cd ..
cd frontend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && cd ..
cp backend/.env.example backend/.env
```

Open `backend/.env` and paste in your keys:

```env
MONGODB_URI=mongodb+srv://...
GOOGLE_API_KEY=...
PINECONE_API_KEY=...
OPENROUTER_API_KEY=...            # or set LLM_FALLBACK_PROVIDER=none to skip it
```

### 3. Start the app

```bash
./run_local.sh
```

Open **http://localhost:8501** in your browser. Press `Ctrl+C` in the terminal
to stop.

## How to use it

### Upload a document

1. Go to **Documents** in the sidebar.
2. Choose a PDF or DOCX file (up to 25 MB) and click **Upload**.
3. Wait until the status shows **ready**. The page refreshes by itself, and a
   short summary of the document appears when it's done.

You can keep up to 5 documents. Click the trash icon to delete one; this also
deletes its chats and quizzes.

### Ask questions

1. Go to **Chat** and pick your document at the top.
2. Type a question in the box at the bottom, for example
   *"Explain photosynthesis in simple words"* or *"What are the main points of chapter 2?"*
3. The answer lists the pages it used. If the document doesn't cover your
   question, the assistant says so instead of guessing.

Turn on **Search the web** to add extra information from the internet. Web
results are labeled separately, and you get 20 web searches per day.

Your chats are saved in the sidebar. Click **New chat** to start fresh, or
click an old chat to continue it.

### Take a quiz

1. Go to **Quiz** and pick your document.
2. In **New quiz**, set:
   - **Topic** (optional): leave it empty to cover the whole document
   - **Difficulty**: easy, medium or hard
   - **Number of questions**: 1 to 10
   - **Question type**: multiple choice or short answer
3. Click **Generate quiz**. This can take up to a minute.
4. Answer the questions and click **Submit answers** to see your score, the
   correct answers, and the page each question came from.

Open **Past quizzes** to review old results or retake a quiz.

## Tips

- **Answers are slow sometimes.** The app uses free AI services with request
  limits. A chat answer can take several seconds; a quiz up to a minute.
- **Don't upload private files.** Free AI services may use the data to improve
  their products.
- **Scanned PDFs (photos of pages) won't work.** The file needs real,
  selectable text.
- **Wrong key used?** If your shell already exports a `GOOGLE_API_KEY`, it
  overrides `backend/.env`. `run_local.sh` handles this for you.

## Learn how it works

Technical details live in the [docs](docs/) folder. Open these in a browser:

- [Project overview](docs/project-flow.html): architecture, API, config, tests
- [Upload & processing](docs/ingestion-flow.html)
- [Chunking strategy](docs/chunking-strategy.html)
- [Chat](docs/chat-flow.html)
- [Quiz](docs/quiz-flow.html)
- [Setup & deployment reference](docs/setup-and-deployment.md)
