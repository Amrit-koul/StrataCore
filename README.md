# QueryMind — AI SQL Assistant 🚀

An end-to-end AI system that converts natural language into SQL queries using a fine-tuned LLM.

---

## 📁 Project Structure

```
sql_assistant/
├── app.py              # Flask web app (main entry point)
├── templates/
│   └── index.html      # Beautiful dark UI
├── train_model.py      # Fine-tune Flan-T5 on custom dataset
├── model_utils.py      # Local model inference helper
├── data.json           # 50+ training examples (input → SQL)
├── requirements.txt    # Python dependencies
└── store.db            # Auto-created SQLite database
```

---

## ⚡ Quick Start (Using Claude API)

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your API key
```bash
export ANTHROPIC_API_KEY=your_key_here
```

### 3. Run the app
```bash
python app.py
```

### 4. Open in browser
```
http://localhost:5000
```

---

## 🤖 LLM Fine-Tuning (Optional — for viva/project demo)

### 1. Install training dependencies
```bash
pip install transformers datasets torch
```

### 2. Train the model
```bash
python train_model.py
```
This fine-tunes `google/flan-t5-small` on your `data.json` examples.

### 3. Use local model in app.py
Replace in `app.py`:
```python
# Instead of Claude API:
from model_utils import generate_sql_local
sql = generate_sql_local(question)
```

---

## 🎯 Features

- **Natural Language → SQL** via Claude AI (prompt engineering)
- **Step-by-step SQL explanation** for each query
- **Visual schema graph** showing table relationships
- **Safety filter** blocks DELETE/DROP/UPDATE queries
- **Beautiful dark UI** with syntax highlighting
- **Fine-tuned LLM** (Flan-T5) for offline SQL generation

---

## 🗄️ Database Schema

```sql
customers(id, name)
orders(id, customer_id, amount, date)
```

Pre-seeded with 6 customers and 10 orders.

---

## 💬 What to Say in Viva

**Q: What LLM did you use?**
> "I used Claude (claude-sonnet) via the Anthropic API with carefully engineered prompts. I also fine-tuned a Flan-T5 transformer on a custom dataset of 50+ question-SQL pairs."

**Q: What is prompt engineering?**
> "Prompt engineering is designing the input to the LLM to guide its output. I included schema, rules, and examples to constrain the model to generate only valid, safe SQL."

**Q: Why not train from scratch?**
> "Training from scratch requires massive data and GPU compute. Fine-tuning a pre-trained model is efficient and domain-specific."

**Q: How do you prevent harmful queries?**
> "I implemented a safety filter that blocks DELETE, DROP, UPDATE, INSERT, ALTER, and TRUNCATE keywords before execution."

---

## 🔬 Evaluation Metrics

Run after fine-tuning:
```python
from model_utils import generate_sql_local

test_cases = [
    ("total sales", "SELECT SUM(amount) FROM orders;"),
    ("list all customers", "SELECT * FROM customers;"),
]

correct = 0
for question, expected in test_cases:
    predicted = generate_sql_local(question).strip().lower()
    if predicted == expected.strip().lower():
        correct += 1

print(f"Accuracy: {correct}/{len(test_cases)} = {correct/len(test_cases)*100:.1f}%")
```
