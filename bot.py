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
from difflib import SequenceMatcher
from typing import List, Dict, Any

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Carregar variáveis de ambiente
load_dotenv()

# Configurações
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Variáveis globais
model = None
db = None
bot_running = threading.Event()
user_state = {}  # Dicionário para rastrear o estado do usuário

class KnowledgeBaseSolver:
    def __init__(self, firestore_client):
        self.db = firestore_client
        self.max_historical_solutions = 5
        self.similarity_threshold = 0.6

    def calcular_similaridade_textual(self, texto1: str, texto2: str) -> float:
        return SequenceMatcher(None, texto1.lower(), texto2.lower()).ratio()

    def extrair_palavras_chave(self, texto: str) -> List[str]:
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
        # Remover marcações redundantes
        texto = texto.replace('**', '')
        
        # Dividir o texto em seções mantendo quebras de linha
        linhas = texto.split('\n')
        texto_formatado = []
        
        # Flags de controle
        em_lista = False
        em_procedimento = False
        paragrafo_atual = []
        
        for linha in linhas:
            linha = linha.strip()
            
            # Pular linhas completamente vazias
            if not linha:
                # Adicionar parágrafo atual se existir
                if paragrafo_atual:
                    texto_formatado.append('\n'.join(paragrafo_atual))
                    paragrafo_atual = []
                # Adicionar linha em branco para manter espaçamento
                texto_formatado.append('')
                continue
            
            # Processamento de listas
            if linha.startswith(('*', '-', '•')):
                linha_limpa = linha.lstrip('*-•').strip()
                paragrafo_atual.append(f'🔹 {linha_limpa}')
                em_lista = True
            
            # Processamento de procedimentos numerados
            elif re.match(r'^\d+\.', linha):
                paragrafo_atual.append(f'{linha}')
                em_procedimento = True
            
            # Conteúdo normal
            else:
                # Se estávamos em uma lista ou procedimento, fechamos
                if em_lista or em_procedimento:
                    texto_formatado.append('\n'.join(paragrafo_atual))
                    paragrafo_atual = []
                    em_lista = False
                    em_procedimento = False
                
                paragrafo_atual.append(linha)
        
        # Adicionar último parágrafo se existir
        if paragrafo_atual:
            texto_formatado.append('\n'.join(paragrafo_atual))
        
        # Juntar o texto formatado
        texto_final = '\n\n'.join(texto_formatado)
        
        # Tratamento HTML
        texto_final = html.escape(texto_final, quote=False)
        tags_permitidas = ['b', 'i', 'u', 'code', 'pre']
        for tag in tags_permitidas:
            texto_final = texto_final.replace(f'&lt;{tag}&gt;', f'<{tag}>')
            texto_final = texto_final.replace(f'&lt;/{tag}&gt;', f'</{tag}>')
        
        # Remover espaços em branco excessivos, mas manter pelo menos duas quebras de linha
        texto_final = re.sub(r'\n{3,}', '\n\n', texto_final)
        
        # Adicionar rodapé técnico
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
        # Se a próxima linha ultrapassar o limite, criar nova mensagem
        if len(mensagem_atual) + len(paragrafo) + 2 > max_length:
            mensagens.append(mensagem_atual.strip())
            mensagem_atual = ""
        
        # Adicionar linha à mensagem atual
        if mensagem_atual:
            mensagem_atual += "\n"
        mensagem_atual += paragrafo
    
    # Adicionar última mensagem
    if mensagem_atual:
        mensagens.append(mensagem_atual.strip())
    
    return mensagens

# Configuração do Gemini
def configurar_gemini():
    global model
    try:
        logger.info("Iniciando configuração do Gemini")
        genai.configure(api_key=GOOGLE_API_KEY)
        
        # Lista de modelos recomendados para substituição
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
                
                # Teste rápido de geração de conteúdo
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

# Configuração do Firestore
def configurar_firestore():
    global db
    try:
        # Usar variável de ambiente para credenciais
        credentials_json = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
        
        if credentials_json:
            # Converter string JSON para dicionário
            creds_dict = json.loads(credentials_json)
            
            # Configurar credenciais
            credentials = service_account.Credentials.from_service_account_info(creds_dict)
            
            # Inicializar Firestore com credenciais
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

# Salvar manutenção no Firestore
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

# Retorna mensagem de erro se a IA falhar
def fallback_diagnostico(equipamento, problema):
    logger.warning(f"Gerando diagnóstico de fallback para {equipamento}")
    
    return f"""
❌ <b>Não foi possível processar sua consulta.</b>

Tente novamente iniciando um novo atendimento com o comando:
👉 /start

<i>Se o problema persistir, revise os dados informados ou reporte ao supervisor.</i>
"""

# Buscar soluções anteriores no Firestore
def buscar_solucoes_anteriores(equipamento):
    try:
        manutencoes_ref = db.collection('manutencoes')
        query = manutencoes_ref.where('equipamento', '==', equipamento).order_by('data', direction=firestore.Query.DESCENDING).limit(5)
        solucoes = [doc.to_dict() for doc in query.stream()]
        return solucoes
    except Exception as e:
        logger.error(f"Erro ao buscar soluções anteriores: {e}")
        return []

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
- Em sua resposta, siga o modelo abaixo:

🔧 DIAGNÓSTICO TÉCNICO

❗ <b>PROBLEMA IDENTIFICADO</b>
Descrição técnica do problema...

📋 <b>ANÁLISE TÉCNICA APROFUNDADA</b>
Detalhamento técnico...

🔍 <b>CAUSAS PROVÁVEIS</b>
🔹 Causa técnica específica
🔹 Outra possível causa... (citar no mínimo 3)

🛠️ <b>PROCEDIMENTO DIAGNÓSTICO</b>
1. Primeiro passo de diagnóstico
2. Segundo passo... (citar no mínimo 3)
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
        
        # Adicionar contexto histórico
        knowledge_solver = KnowledgeBaseSolver(db)
        solucoes_historicas = knowledge_solver.buscar_solucoes_contextualizadas(
            equipamento, problema
        )
        
        solucao_contextualizada = knowledge_solver.enriquecer_diagnostico(
            resposta.text, solucoes_historicas
        )
        
        texto_resposta = sanitizar_html(solucao_contextualizada)
        
        logger.info("Resposta do Gemini recebida com sucesso")
        return texto_resposta
    
    except Exception as e:
        logger.error(f"Erro na consulta de IA: {e}", exc_info=True)
        return fallback_diagnostico(equipamento, problema)

# Telegram Bot - Configuração
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML')

@bot.message_handler(commands=['start'])
def mensagem_inicial(message):
    logger.info(f"Comando /start recebido de {message.from_user.username}")
    
    # Resetar o estado do usuário
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
    user_id = message.from_user.id
    
    # Adicionar log para debug
    logger.info(f"Mensagem recebida. User ID: {user_id}, Stage: {user_state.get(user_id, {}).get('stage', 'Não definido')}")
    
    # Se o usuário não tiver estado definido, inicializar
    if user_id not in user_state:
        user_state[user_id] = {'stage': 'intro'}
    
    try:
        current_stage = user_state[user_id].get('stage', 'intro')
        
        if current_stage == 'intro':
            # Capturar informações do equipamento
            equipamento = message.text.strip()
            
            # Validar se a mensagem não está vazia
            if not equipamento:
                bot.reply_to(message, "❌ Por favor, informe os detalhes do equipamento.")
                return
            
            # Log de debug
            logger.info(f"Equipamento capturado: {equipamento}")
            
            # Salvar informações do equipamento e mudar para próximo estágio
            user_state[user_id] = {
                'stage': 'problem_description',
                'equipamento': equipamento
            }
            
            # Solicitar descrição do problema
            bot.reply_to(message, 
                f"✅ Equipamento registrado: <b>{equipamento}</b>\n\n"
                "Agora, descreva detalhadamente o problema que você está enfrentando. "
                "Seja o mais específico possível sobre os sintomas, comportamentos incomuns, "
                "sons, ou qualquer outra observação relevante."
            )
        
        elif current_stage == 'problem_description':
            # Capturar descrição do problema
            problema = message.text.strip()
            
            # Validar se a descrição não está vazia
            if not problema:
                bot.reply_to(message, "❌ Por favor, descreva o problema em detalhes.")
                return
            
            # Log de debug
            logger.info(f"Problema capturado: {problema}")
            
            # Buscar solução via IA
            equipamento = user_state[user_id]['equipamento']
            solucao = buscar_solucao_ia(equipamento, problema)
            
            # Dividir mensagem
            mensagens = dividir_mensagem(solucao)
            
            # Criar primeira mensagem com cabeçalho
            primeira_mensagem = f"🔧 Diagnóstico para {equipamento}"
            
            # Enviar primeira mensagem (cabeçalho + primeiro conteúdo)
            if mensagens:
                bot.reply_to(message, f"{primeira_mensagem}\n\n{mensagens[0]}")
            
            # Enviar mensagens subsequentes
            for msg_adicional in mensagens[1:]:
                bot.send_message(message.chat.id, msg_adicional)
            
            # Solicitar feedback
            user_state[user_id] = {
                'stage': 'feedback',
                'equipamento': equipamento,
                'problema': problema,
                'solucao': solucao  # Manter solução atual
            }
            
            bot.send_message(message.chat.id, 
                "A solução foi útil?\n"
                "Responda:\n"
                "✅ SIM - se a solução resolveu o problema\n"
                "❌ NÃO - se precisou de outras ações"
            )
        
        elif current_stage == 'feedback':
            feedback = message.text.strip().lower()
            
            if feedback in ['✅', 'sim']:
                # Aqui salva no Firestore somente com feedback positivo
                solucao = user_state[user_id]['solucao']
                equipamento = user_state[user_id]['equipamento']
                problema = user_state[user_id]['problema']
                
                salvar_manutencao(equipamento, problema, solucao)
                
                bot.reply_to(message, 
                    "Ótimo! Fico feliz em ter ajudado. 👍\n"
                    "Solução salva para futuras consultas.\n"
                    "Se precisar de mais alguma coisa, use /start."
                )
                # Resetar estado
                user_state[user_id] = {'stage': 'intro'}
            
            elif feedback in ['❌', 'não']:
                bot.reply_to(message, 
                    "Peço desculpas que a solução não foi completamente efetiva. 🤔\n"
                    "Por favor, descreva:\n"
                    "1. Qual era o DEFEITO ESPECÍFICO?\n"
                    "2. Qual SOLUÇÃO VOCÊ ENCONTROU?"
                )
                # Preparar para registrar informação adicional
                user_state[user_id]['stage'] = 'solution_refinement'
            
            else:
                bot.reply_to(message, 
                    "Desculpe, não entendi sua resposta. 🤨\n"
                    "Por favor, responda com ✅ SIM ou ❌ NÃO"
                )
        
        elif current_stage == 'solution_refinement':
            # Processar texto com detalhes da solução refinada
            informacao_adicional = message.text.strip()
            
            # Tentar gerar nova solução com informações adicionais
            try:
                equipamento = user_state[user_id]['equipamento']
                problema_original = user_state[user_id]['problema']
                
                # Prompt para refinar a solução
                prompt_refinamento = f"""
CONTEXTO ANTERIOR:
Equipamento: {equipamento}
Problema Original: {problema_original}

NOVA INFORMAÇÃO DO TÉCNICO:
{informacao_adicional}

Por favor, gere uma solução técnica ATUALIZADA e MAIS ESPECÍFICA considerando 
as novas informações fornecidas.
"""
                
                # Gerar solução refinada
                solucao_refinada = buscar_solucao_ia(equipamento, prompt_refinamento)
                
                # Salvar solução refinada no Firestore
                salvar_manutencao(equipamento, problema_original, solucao_refinada)
                
                # Dividir mensagem refinada
                mensagens_refinadas = dividir_mensagem(solucao_refinada)
                
                # Enviar mensagens
                bot.reply_to(message, "🔍 Solução Refinada:")
                for msg in mensagens_refinadas:
                    bot.send_message(message.chat.id, msg)
                
                bot.send_message(message.chat.id, 
                    "Esta solução atende suas necessidades?\n"
                    "✅ SIM - solução satisfatória\n"
                    "❌ NÃO - precisamos revisar novamente"
                )
                
                # Atualizar estado
                user_state[user_id] = {
                    'stage': 'feedback_refinado',
                    'equipamento': equipamento,
                    'problema': problema_original,
                    'solucao': solucao_refinada
                }
            
            except Exception as e:
                logger.error(f"Erro no refinamento da solução: {e}")
                bot.reply_to(message, "Desculpe, não foi possível refinar a solução no momento.")
                user_state[user_id] = {'stage': 'intro'}
        
        elif current_stage == 'feedback_refinado':
            feedback = message.text.strip().lower()
            
            if feedback in ['✅', 'sim']:
                bot.reply_to(message, 
                    "Ótimo! Solução refinada salva. 👍\n"
                    "Se precisar de mais alguma coisa, use /start."
                )
                # Resetar estado
                user_state[user_id] = {'stage': 'intro'}
            
            elif feedback in ['❌', 'não']:
                bot.reply_to(message, 
                    "Entendi que a solução ainda não atende completamente. 🤔\n"
                    "Por favor, descreva novamente o problema específico."
                )
                # Voltar para refinamento
                user_state[user_id]['stage'] = 'solution_refinement'
            
            else:
                bot.reply_to(message, 
                    "Desculpe, não entendi sua resposta. 🤨\n"
                    "Por favor, responda com ✅ SIM ou ❌ NÃO"
                )
    
    except Exception as e:
        logger.error(f"Erro detalhado ao processar: {e}", exc_info=True)
        bot.reply_to(message, f"Desculpe, ocorreu um erro: {str(e)}")
        # Resetar estado em caso de erro
        user_state[user_id] = {'stage': 'intro'}

def start_bot():
    tentativas = 0
    max_tentativas = 5
    while not bot_running.is_set() and tentativas < max_tentativas:
        try:
            logger.info(f"Tentativa {tentativas + 1} de iniciar o bot")
            bot.remove_webhook()
            
            # Adicionar polling com parâmetros mais robustos
            bot.polling(
                none_stop=True, 
                timeout=90, 
                long_polling_timeout=90,
                skip_pending=True  # Ignorar updates pendentes
            )
            
            bot_running.set()
        except telebot.apihelper.ApiException as e:
            logger.error(f"Erro de API do Telegram: {e}")
            if e.result.status_code == 409:
                logger.warning("Conflito de sessão detectado. Aguardando e tentando novamente...")
                time.sleep(10)  # Aguardar antes de tentar novamente
            tentativas += 1
        except Exception as e:
            logger.critical(f"Erro no polling do bot: {e}", exc_info=True)
            time.sleep(10)
            tentativas += 1
    
    if tentativas >= max_tentativas:
        logger.critical("Falha ao iniciar o bot após múltiplas tentativas")
        sys.exit(1)

def main():
    # Configurações iniciais
    gemini_ok = configurar_gemini()
    firestore_ok = configurar_firestore()
    
    if not (gemini_ok and firestore_ok):
        logger.critical("Falha em configurar serviços. Encerrando.")
        return
    
    logger.info("Inicializando bot de suporte técnico...")
    
    # Inicia o bot em uma thread separada
    bot_thread = threading.Thread(target=start_bot)
    bot_thread.start()

    # Manter o programa principal rodando
    try:
        bot_thread.join()
    except KeyboardInterrupt:
        logger.info("Encerrando bot...")
        bot_running.set()
        bot_thread.join()

if __name__ == '__main__':
    main()