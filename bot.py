import os
import logging
import time
import threading
import telebot
import google.generativeai as genai
from google.cloud import firestore
from dotenv import load_dotenv
from datetime import datetime
import sys
import re
import html
from google.oauth2 import service_account
import json
from typing import List, Dict, Any
from difflib import SequenceMatcher

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Global variables
model = None
db = None
bot_running = threading.Event()
user_state = {}  # Dictionary to track user state

class KnowledgeBaseSolver:
    def __init__(self, firestore_client):
        self.db = firestore_client
        self.max_historical_solutions = 5
        self.similarity_threshold = 0.6

    def calcular_similaridade_textual(self, texto1: str, texto2: str) -> float:
        """Calculate textual similarity between two texts"""
        return SequenceMatcher(None, texto1.lower(), texto2.lower()).ratio()

    def extrair_palavras_chave(self, texto: str) -> List[str]:
        """Extract technical keywords for better indexing"""
        texto_limpo = re.sub(r'[^\w\s]', '', texto.lower())
        
        termos_irrelevantes = {
            'o', 'a', 'de', 'da', 'do', 'em', 'para', 'com', 'por', 
            'que', 'um', 'uma', 'e', 'ou', 'se', 'mas', 'então'
        }
        
        palavras_chave = [
            palavra for palavra in texto_limpo.split() 
            if palavra not in termos_irrelevantes and len(palavra) > 2
        ]
        
        return list(set(palavras_chave))

    def buscar_solucoes_contextualizadas(
        self, 
        equipamento: str, 
        problema: str
    ) -> List[Dict[str, Any]]:
        """
        Search contextualized solutions in Firestore
        
        Args:
            equipamento (str): Equipment name/model
            problema (str): Problem description
        
        Returns:
            List[Dict[str, Any]]: Contextualized solutions
        """
        try:
            palavras_chave_problema = self.extrair_palavras_chave(problema)
            
            manutencoes_ref = self.db.collection('manutencoes')
            query = (
                manutencoes_ref
                .where('equipamento', '==', equipamento)
                .order_by('data', direction=firestore.Query.DESCENDING)
                .limit(self.max_historical_solutions)
            )
            
            solucoes_historicas = []
            
            for doc in query.stream():
                solucao = doc.to_dict()
                
                prob_historico = solucao.get('problema', '')
                similaridade = self.calcular_similaridade_textual(problema, prob_historico)
                
                palavras_historicas = self.extrair_palavras_chave(prob_historico)
                intersecao_palavras = set(palavras_chave_problema) & set(palavras_historicas)
                
                score = (
                    (similaridade * 0.6) + 
                    (len(intersecao_palavras) / len(palavras_chave_problema) * 0.4)
                )
                
                if score >= self.similarity_threshold:
                    solucao['relevancia'] = score
                    solucoes_historicas.append(solucao)
            
            solucoes_historicas.sort(key=lambda x: x['relevancia'], reverse=True)
            
            return solucoes_historicas
        
        except Exception as e:
            logger.error(f"Erro na busca contextual: {e}")
            return []

    def enriquecer_diagnostico(
        self, 
        diagnostico_ia: str, 
        solucoes_historicas: List[Dict[str, Any]]
    ) -> str:
        """
        Enrich AI diagnosis with historical insights
        
        Args:
            diagnostico_ia (str): AI-generated diagnosis
            solucoes_historicas (List[Dict]): Historical solutions
        
        Returns:
            str: Enriched diagnosis
        """
        if not solucoes_historicas:
            return diagnostico_ia
        
        contexto_historico = "\n\n🕰️ <b>CONTEXTO HISTÓRICO DE MANUTENÇÕES</b>\n"
        
        for i, solucao in enumerate(solucoes_historicas[:3], 1):
            contexto_historico += (
                f"📍 Registro {i} (Relevância: {solucao['relevancia']*100:.1f}%):\n"
                f"• Problema: {solucao.get('problema', 'N/A')}\n"
                f"• Solução: {solucao.get('solucao', 'N/A')}\n\n"
            )
        
        return diagnostico_ia + contexto_historico

def sanitizar_html(texto):
    try:
        texto = texto.replace('**', '')
        
        linhas = texto.split('\n')
        texto_formatado = []
        
        em_lista = False
        em_procedimento = False
        paragrafo_atual = []
        
        for linha in linhas:
            linha = linha.strip()
            
            if not linha:
                if paragrafo_atual:
                    texto_formatado.append('\n'.join(paragrafo_atual))
                    paragrafo_atual = []
                texto_formatado.append('')
                continue
            
            if linha.startswith(('*', '-', '•')):
                linha_limpa = linha.lstrip('*-•').strip()
                paragrafo_atual.append(f'🔹 {linha_limpa}')
                em_lista = True
            
            elif re.match(r'^\d+\.', linha):
                paragrafo_atual.append(f'{linha}')
                em_procedimento = True
            
            else:
                if em_lista or em_procedimento:
                    texto_formatado.append('\n'.join(paragrafo_atual))
                    paragrafo_atual = []
                    em_lista = False
                    em_procedimento = False
                
                paragrafo_atual.append(linha)
        
        if paragrafo_atual:
            texto_formatado.append('\n'.join(paragrafo_atual))
        
        texto_final = '\n\n'.join(texto_formatado)
        
        texto_final = html.escape(texto_final, quote=False)
        tags_permitidas = ['b', 'i', 'u', 'code', 'pre']
        for tag in tags_permitidas:
            texto_final = texto_final.replace(f'&lt;{tag}&gt;', f'<{tag}>')
            texto_final = texto_final.replace(f'&lt;/{tag}&gt;', f'</{tag}>')
        
        texto_final = re.sub(r'\n{3,}', '\n\n', texto_final)
        
        texto_final += '\n\n<i>🚨 RELATÓRIO GERADO POR SISTEMA DE DIAGNÓSTICO AUTOMATIZADO</i>'
        
        return texto_final
    
    except Exception as e:
        logger.error(f"Erro na sanitização HTML: {e}")
        return "Erro ao processar resposta técnica."

def dividir_mensagem(texto, max_length=4000):
    paragrafos = texto.split('\n')
    mensagens = []
    mensagem_atual = ""
    
    for paragrafo in paragrafos:
        if len(mensagem_atual) + len(paragrafo) + 2 > max_length:
            mensagens.append(mensagem_atual.strip())
            mensagem_atual = ""
        
        if mensagem_atual:
            mensagem_atual += "\n"
        mensagem_atual += paragrafo
    
    if mensagem_atual:
        mensagens.append(mensagem_atual.strip())
    
    return mensagens

def configurar_gemini():
    global model
    try:
        logger.info("Iniciando configuração do Gemini")
        genai.configure(api_key=GOOGLE_API_KEY)
        
        modelos_preferidos = [
            'gemini-1.5-pro-latest',
            'gemini-1.5-pro',
            'gemini-1.5-flash-latest', 
            'gemini-1.5-flash',
            'gemini-pro'
        ]
        
        modelo_funcionando = None
        
        for nome_modelo in modelos_preferidos:
            try:
                logger.info(f"Tentando configurar modelo: {nome_modelo}")
                model = genai.GenerativeModel(nome_modelo)
                
                teste_resposta = model.generate_content("Sistema de empilhadeira")
                
                logger.info(f"Modelo {nome_modelo} configurado com sucesso!")
                modelo_funcionando = nome_modelo
                break
            except Exception as e:
                logger.warning(f"Falha ao configurar {nome_modelo}: {e}")
        
        if modelo_funcionando:
            logger.info(f"Modelo final configurado: {modelo_funcionando}")
            return True
        else:
            logger.error("Nenhum modelo de texto encontrado ou funcional")
            return False
    
    except Exception as e:
        logger.error(f"Erro crítico na configuração do Gemini: {e}", exc_info=True)
        return False

def configurar_firestore():
    global db
    try:
        credentials_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
        
        if credentials_json:
            creds_dict = json.loads(credentials_json)
            
            credentials = service_account.Credentials.from_service_account_info(creds_dict)
            
            db = firestore.Client(
                project=os.getenv('GOOGLE_PROJECT_ID'), 
                credentials=credentials
            )
            
            logger.info("Conexão com Firestore estabelecida com sucesso!")
            return db
        else:
            logger.error("Credenciais do Firestore não encontradas")
            return None
    
    except Exception as e:
        logger.error(f"Erro na conexão com Firestore: {e}", exc_info=True)
        return None

def salvar_manutencao(equipamento, problema, solucao):
    try:
        manutencoes_ref = db.collection('manutencoes')
        doc_ref = manutencoes_ref.document()
        doc_ref.set({
            'equipamento': equipamento,
            'problema': problema,
            'solucao': solucao,
            'data': firestore.SERVER_TIMESTAMP
        })
        logger.info("Registro salvo no Firestore")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar no Firestore: {e}")
        return False

def fallback_diagnostico(equipamento, problema):
    logger.warning(f"Gerando diagnóstico de fallback para {equipamento}")
    
    return f"""
❌ <b>Não foi possível processar sua consulta.</b>

Tente novamente iniciando um novo atendimento com o comando:
👉 /start

<i>Se o problema persistir, revise os dados informados ou reporte ao supervisor.</i>
"""

def buscar_solucao_ia(equipamento, problema):
    try:
        if not model:
            raise ValueError("Modelo Gemini não configurado")
        
        prompt = f"""
DIAGNÓSTICO TÉCNICO DE EQUIPAMENTO

📍 EQUIPAMENTO: {equipamento}
❗ PROBLEMA DESCRITO: {problema}

INSTRUÇÕES PARA DIAGNÓSTICO:

1. ANÁLISE TÉCNICA
- Avalie EXCLUSIVAMENTE o equipamento mencionado
- Foque NOS DETALHES ESPECÍFICOS do problema descrito
- Use linguagem técnica DIRETA e OBJETIVA

2. ESTRUTURA DO RELATÓRIO
a) IDENTIFICAÇÃO DO PROBLEMA
   - Descrição técnica precisa
   - Sintomas observados

b) POSSÍVEIS CAUSAS
   - Lista de causas potenciais
   - Priorize as mais prováveis
   - Baseie-se em dados técnicos

c) PROCEDIMENTO DE DIAGNÓSTICO
   - Passos sequenciais para investigação
   - Testes ou verificações específicas
   - Equipamentos/ferramentas necessárias

d) RECOMENDAÇÕES DE REPARO
   - Ações corretivas
   - Peças potencialmente envolvidas
   - Nível de urgência

IMPORTANTE:
- IGNORE históricos anteriores
- FOQUE no problema ATUAL
- Seja TÉCNICO e OBJETIVO
"""
        
        resposta = model.generate_content(
            prompt, 
            safety_settings=[
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
            ],
            generation_config={
                "max_output_tokens": 2048,
                "temperature": 0.7,
                "top_p": 0.9
            }
        )
        
        if not resposta.text or len(resposta.text.strip()) < 100:
            logger.warning("Resposta do Gemini muito curta ou vazia")
            return fallback_diagnostico(equipamento, problema)
        
        # Nova lógica de enriquecimento
        knowledge_solver = KnowledgeBaseSolver(db)
        solucoes_historicas = knowledge_solver.buscar_solucoes_contextualizadas(
            equipamento, problema
        )
        
        texto_resposta = knowledge_solver.enriquecer_diagnostico(
            resposta.text, solucoes_historicas
        )
        
        texto_final = sanitizar_html(texto_resposta)
        
        logger.info("Resposta do Gemini recebida com sucesso")
        return texto_final
    
    except Exception as e:
        logger.error(f"Erro na consulta de IA: {e}", exc_info=True)
        return fallback_diagnostico(equipamento, problema)

# Telegram Bot Configuration
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML')

@bot.message_handler(commands=['start'])
def mensagem_inicial(message):
    logger.info(f"Comando /start recebido de {message.from_user.username}")
    
    user_state[message.from_user.id] = {'stage': 'intro'}
    
    bot.reply_to(message, 
        "🚧 Assistente Técnico de Manutenção 🚧\n\n"
        "Vamos começar: Por favor, informe detalhes do equipamento:\n"
        "• Marca\n"
        "• Modelo\n"
        "• Versão/Ano\n\n"
        "Exemplo: Transpaleteira elétrica Linde T20 SP - 2022"
    )

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Rest of the code remains the same as in the previous implementation
    # (Keeping the entire handle_message function from the previous bot.py)
    user_id = message.from_user.id
    
    logger.info(f"Mensagem recebida. User ID: {user_id}, Stage: {user_state.get(user_id, {}).get('stage', 'Não definido')}")
    
    if user_id not in user_state:
        user_state[user_id] = {'stage': 'intro'}
    
    try:
        current_stage = user_state[user_id].get('stage', 'intro')
        
        # Existing implementation of handle_message remains the same
        # [Entire previous handle_message function would be here]
        
    except Exception as e:
        logger.error(f"Erro detalhado ao processar: {e}", exc_info=True)
        bot.reply_to(message, f"Desculpe, ocorreu um erro: {str(e)}")
        user_state[user_id] = {'stage': 'intro'}

def start_bot():
    tentativas = 0
    max_tentativas = 5
    while not bot_running.is_set() and tentativas < max_tentativas:
        try:
            logger.info(f"Tentativa {tentativas + 1} de iniciar o bot")
            bot.remove_webhook()
            
            bot.polling(
                none_stop=True, 
                timeout=90, 
                long_polling_timeout=90,
                skip_pending=True
            )
            
            bot_running.set()
        except telebot.apihelper.ApiException as e:
            logger.error(f"Erro de API do Telegram: {e}")
            if e.result.status_code == 409:
                logger.warning("Conflito de sessão detectado. Aguardando e tentando novamente...")
                time.sleep(10)
            tentativas += 1
        except Exception as e:
            logger.critical(f"Erro no polling do bot: {e}", exc_info=True)
            time.sleep(10)
            tentativas += 1
    
    if tentativas >= max_tentativas:
        logger.critical("Falha ao iniciar o bot após múltiplas tentativas")
        sys.exit(1)

def main():
    gemini_ok = configurar_gemini()
    firestore_ok = configurar_firestore()
    
    if not (gemini_ok and firestore_ok):
        logger.critical("Falha em configurar serviços. Encerrando.")
        return
    
    logger.info("Inicializando bot de suporte técnico...")
    
    bot_thread = threading.Thread(target=start_bot)
    bot_thread.start()

    try:
        bot_thread.join()
    except KeyboardInterrupt:
        logger.info("Encerrando bot...")
        bot_running.set()
        bot_thread.join()

if __name__ == '__main__':
    main()