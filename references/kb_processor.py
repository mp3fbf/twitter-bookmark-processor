#!/usr/bin/env python3
"""
Processador de emails para base de conhecimento.
Processa UM POR UM do mais antigo ao mais novo.
"""

import re
import json
from datetime import datetime
from pathlib import Path

# Paths
EMAILS_DIR = Path("/workspace/notes/email-analysis/emails-content")
STATE_FILE = Path("/workspace/notes/.email-kb-progress.json")
RUIDO_FILE = Path("/workspace/notes/.email-ruido-ids.txt")
DCT_FILE = Path("/workspace/notes/.email-dct-ids.txt")

# === FILTROS DE RUIDO ===
RUIDO_REMETENTES = [
    "rogeria.bezerra@",
    "juliana.sotto@",
    "matheus.mandarano@",
    "farmacia",
    "compras",
    "comunicados@",
    "troquesuasenha@",
    "noreply@",
]

RUIDO_DOMINIOS = [
    "@rvdsaude.com.br",
    "@seroplast.com.br",
    "@rvimola.com.br",
    "@3m.com",
    "@medtronic.com",
    "@baxter.com",
]

RUIDO_ASSUNTOS = [
    "pedido 45",
    "entrega atrasada",
    "nota fiscal",
    "pagamento",
    "boleto",
    "mat especifico",
    "medicamento atrasado",
    "nf ",
    "nfe ",
]


def parse_emails_from_md(filepath):
    """Parse emails from markdown file."""
    content = filepath.read_text()
    emails = []

    # Split by email separator
    parts = re.split(r'={80}', content)

    current_email = None
    for part in parts:
        if 'EMAIL ID:' in part:
            # Extract ID
            id_match = re.search(r'EMAIL ID:\s*(\w+)', part)
            if id_match:
                current_email = {'id': id_match.group(1), 'raw': ''}
        elif current_email:
            # This is the content
            current_email['raw'] = part.strip()

            # Parse header fields
            subject_match = re.search(r'^# (.+)$', part, re.MULTILINE)
            from_match = re.search(r'\*\*From:\*\*\s*(.+)$', part, re.MULTILINE)
            date_match = re.search(r'\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', part)

            if subject_match:
                current_email['subject'] = subject_match.group(1).strip()
            if from_match:
                current_email['from'] = from_match.group(1).strip()
            if date_match:
                current_email['date'] = date_match.group(1)

            if 'date' in current_email and 'subject' in current_email:
                emails.append(current_email)
            current_email = None

    return emails


def is_ruido(email):
    """Verifica se email é ruído (rápido, sem ler corpo)."""
    from_addr = email.get('from', '').lower()
    subject = email.get('subject', '').lower()

    # Check remetente
    for ruido in RUIDO_REMETENTES:
        if ruido.lower() in from_addr:
            return True, f"remetente:{ruido}"

    # Check dominio externo
    for dominio in RUIDO_DOMINIOS:
        if dominio.lower() in from_addr:
            return True, f"dominio:{dominio}"

    # Check assunto
    for ruido in RUIDO_ASSUNTOS:
        if ruido.lower() in subject:
            return True, f"assunto:{ruido}"

    return False, None


def load_all_emails():
    """Load all emails from both MD files."""
    all_emails = []

    # Feb-Mar 2023
    if (EMAILS_DIR / "all_emails.md").exists():
        all_emails.extend(parse_emails_from_md(EMAILS_DIR / "all_emails.md"))

    # April 2023
    if (EMAILS_DIR / "2023-04-emails.md").exists():
        all_emails.extend(parse_emails_from_md(EMAILS_DIR / "2023-04-emails.md"))

    # Sort by date
    def parse_date(e):
        try:
            return datetime.strptime(e['date'], '%Y-%m-%d %H:%M')
        except:
            return datetime.min

    all_emails.sort(key=parse_date)
    return all_emails


def load_state():
    """Load processing state."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        'processed_count': 0,
        'ruido_count': 0,
        'dct_count': 0,
        'last_processed_idx': -1
    }


def save_state(state):
    """Save processing state."""
    STATE_FILE.write_text(json.dumps(state, indent=2))


def main():
    """Load and show stats."""
    print("Carregando emails...")
    emails = load_all_emails()
    print(f"Total de emails: {len(emails)}")

    state = load_state()
    print(f"Já processados: {state['processed_count']}")
    print(f"Ruído pulado: {state['ruido_count']}")
    print(f"DCT analisados: {state['dct_count']}")
    print(f"Próximo a processar: índice {state['last_processed_idx'] + 1}")

    # Show first few
    print("\n--- Primeiros 5 emails (mais antigos) ---")
    for i, e in enumerate(emails[:5]):
        ruido, motivo = is_ruido(e)
        status = f"RUÍDO ({motivo})" if ruido else "DCT"
        print(f"{i+1}. [{e['date']}] {status}")
        print(f"   De: {e.get('from', 'N/A')[:60]}")
        print(f"   Assunto: {e.get('subject', 'N/A')[:60]}")
        print()


if __name__ == "__main__":
    main()
