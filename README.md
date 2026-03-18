🚀 Multithreaded Trading Bot: Binance & Bitmex
A High-Performance FinTech System built with Python & Tkinter.

📖 Project Overview
This project is a real-time trading dashboard designed to bridge the gap between academic theory and production-grade software engineering. As a HyperionDev Alumna, I developed this system to master complex concepts like concurrency, asynchronous data ingestion, and decoupled UI architecture.

The bot maintains a persistent connection to exchange WebSockets, allowing for sub-100ms price updates without interrupting the user interface.

🛠️ Technical Architecture
To ensure scalability and maintainability, the project follows a modular "Flat-to-Folder" structure:

Concurrency Model: Uses Python’s threading library to run a dedicated "Worker Thread" for API data, preventing Tkinter "Screen Freeze."

Data Integrity: Implements thread-safe Queues and Type Hinting to ensure data consistency between the back-end and the UI.

API Resilience: Custom error-handling logic to manage WebSocket "kicks," rate-limiting, and network latency.

Decoupled UI: A dedicated styling.py and component-based interface for easy theme customization and scaling.

📂 File Map & System Logic
main.py: The system entry point; manages the lifecycle of the application and background threads.

binance_client.py / bitmex_client.py: Handles REST and WebSocket authentication and data streaming.

styling.py: Centralized UI configuration for a professional, dark-themed dashboard.

models/: (Folder) Contains object blueprints for Tick, Contract, and Order data structures.

📊 Agile Roadmap & Mentorship Focus
I maintain a transparent development process using a GitHub Project Board. This reflects my ability to break down complex engineering challenges into manageable milestones—a core skill I bring to the HyperionDev Facilitation Team.

Featured Roadmap Labels:

Educational-Demo: Tasks specifically documented to serve as teaching resources.

⚠️ Attribution & Usage Notice
This repository is a private professional portfolio piece. > The code is provided strictly for technical review and educational demonstration (specifically for the HyperionDev Facilitator application).

No License for Use: I do not grant permission for this code to be used for live trading, redistributed, or used in commercial projects.

Liability: I am not responsible for any financial losses incurred by individuals attempting to replicate this logic.

Security: Never share or upload your own API keys. This project uses a .env system to keep credentials local and secure.

Architecture: Core engineering wins regarding multithreading and stability.

Performance: Optimizations for real-time data ingestion.
