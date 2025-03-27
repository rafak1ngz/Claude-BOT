import os
import logging
import time
import threading
import telebot
import google.generativeai as genai
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
from datetime import datetime
import sys
import re
import html

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
MONGODB_URI = os.getenv('MONGODB_URI')

# Variáveis globais
model = None
manutencoes_collection = None
bot_running = threading.Event()
user_state = {}  # Dicionário para rastrear o estado do usuário

# Função de sanitização de HTML melhorada
def sanitizar_html(texto):
    try:
        # Remover cabeçalhos duplicados
        linhas = texto.split('\n')
        linhas_unicas = []
        titulos_vistos = set()
        
        for linha in linhas:
            linha = linha.strip()
            
            # Remover linhas de código e cabeçalhos HTML
            if linha.startswith('```html') or linha.startswith('<!DOCTYPE') or linha.startswith('<html'):
                continue
            
            # Filtrar títulos duplicados
            if linha.startswith('Diagnóstico Técnico'):
                if linha not in titulos_vistos:
                    titulos_vistos.add(linha)
                else:
                    continue
            
            linhas_unicas.append(linha)
        
        # Remover emojis duplicados no cabeçalho
        for i in range(len(linhas_unicas)):
            if linhas_unicas[i].startswith('🔧 Diagnóstico'):
                linhas_unicas[i] = linhas_unicas[i].split('🚨')[0].strip()
        
        # Juntar linhas
        texto = '\n'.join(linhas_unicas)
        
        # Limpar formatações extras
        texto = re.sub(r'```', '', texto)
        
        return texto.strip()
    
    except Exception as e:
        logger.error(f"Erro na sanitização HTML: {e}")
        return "Erro ao processar resposta técnica."

def dividir_mensagem(texto, max_length=4000):
    # Dividir mensagem mantendo a estrutura
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
            return True
        else:
            logger.error("Nenhum modelo de texto encontrado ou funcional")
            return False
    
    except Exception as e:
        logger.error(f"Erro crítico na configuração do Gemini: {e}", exc_info=True)
        return False

# Configuração do MongoDB
def configurar_mongodb():
    global manutencoes_collection
    try:
        logger.info("Iniciando conexão com MongoDB")
        # Configurações SSL mais flexíveis
        mongo_client = MongoClient(
            MONGODB_URI, 
            server_api=ServerApi('1'), 
            tls=True,
            tlsAllowInvalidCertificates=True,  # Permite certificados inválidos
            socketTimeoutMS=30000,  # Timeout de 30 segundos
            connectTimeoutMS=30000,
            serverSelectionTimeoutMS=30000,
            waitQueueTimeoutMS=30000
        )
        
        db = mongo_client['empilhadeiras_db']
        manutencoes_collection = db['manutencoes']
        logger.info("Conexão com MongoDB estabelecida com sucesso!")
        return True
    except Exception as e:
        logger.error(f"Erro na conexão com MongoDB: {e}", exc_info=True)
        return False

# Inicializar Telegram Bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='HTML')

def buscar_solucao_ia(equipamento, problema):
    try:
        if not model:
            raise ValueError("Modelo Gemini não configurado")
        
        prompt = f"""
        // Informações para diagnóstico único
        Foque EXCLUSIVAMENTE nesta situação específica:
        Equipamento: {equipamento}
        Descrição do Problema: {problema}

        // Objetivo
        Gere um diagnóstico técnico CURTO e DIRETO em HTML
        
        //Modelo a ser respondido
        1. Análise do problema reportado
        2. Possíveis causas da falha
        3. Procedimento de diagnóstico
        4. Passos para reparo ou manutenção
        5. Peças potencialmente envolvidas //(informar com código do fabricante)

        // Regras importantes:
        • IGNORE qualquer contexto ou problema anterior
        • Concentre-se APENAS no problema atual descrito
        • Responda considerando SOMENTE as informações atuais
        
        // Regras de formatação HTML
        • Use <b>negrito</b> para títulos
        • Use <i>itálico</i> para ênfases
        • Utilize <br> para quebras de linha
        • Crie listas com • no início de cada item
        • Seja técnico e direto
        """
        
        logger.info(f"Enviando prompt para Gemini")
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
        
        resposta = model.generate_content(
            prompt, 
            safety_settings=safety_settings,
            generation_config={
                "max_output_tokens": 2048,
                "temperature": 0.5,
                "top_p": 1
            }
        )
        
        # Sanitizar a resposta HTML
        texto_resposta = sanitizar_html(resposta.text)
        
        # Adicionar emoji para dar mais personalidade
        texto_formatado = f"🔧 Diagnóstico para {equipamento} 🚨\n\n{texto_resposta}"
        
        logger.info("Resposta do Gemini recebida")
        return texto_formatado
    
    except Exception as e:
        logger.error(f"Erro na consulta de IA: {e}", exc_info=True)
        return f"🚫 Ops! Não consegui processar o diagnóstico. Erro: {str(e)} 😓"

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
    
    # Se o usuário não tiver estado definido ou estiver fora do fluxo correto, reiniciar
    if (user_id not in user_state or 
        user_state[user_id].get('stage') not in ['intro', 'problem_description']):
        # Sempre redirecionar para a mensagem inicial
        bot.reply_to(message, 
            "🚧 Assistente Técnico de Manutenção 🚧\n\n"
            "Vamos começar: Por favor, informe detalhes do equipamento:\n"
            "• Marca\n"
            "• Modelo\n"
            "• Versão/Ano\n\n"
            "Exemplo: Transpaleteira elétrica Linde T20 SP - 2022"
        )
        # Resetar o estado para o estágio inicial
        user_state[user_id] = {'stage': 'intro'}
        return
    
    try:
        current_stage = user_state[user_id].get('stage')
        
        if current_stage == 'intro':
            # Capturar informações do equipamento
            equipamento = message.text.strip()
            
            # Validar se a mensagem não está vazia
            if not equipamento:
                bot.reply_to(message, "❌ Por favor, informe os detalhes do equipamento.")
                return
            
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
            
            # Buscar solução via IA
            equipamento = user_state[user_id]['equipamento']
            solucao = buscar_solucao_ia(equipamento, problema)
            
            # Dividir mensagem longa em partes
            mensagens = dividir_mensagem(solucao)
            
            # Enviar primeira mensagem
            primeira_mensagem = f"🔧 Diagnóstico para {equipamento}:\n\n{mensagens[0]}"
            bot.reply_to(message, primeira_mensagem)
            
            # Enviar mensagens subsequentes
            for msg_adicional in mensagens[1:]:
                bot.send_message(message.chat.id, msg_adicional)
            
            # Salvar no banco de dados (opcional)
            if manutencoes_collection is not None:
                try:
                    registro = {
                        'equipamento': equipamento,
                        'problema': problema,
                        'solucao': solucao,
                        'data': datetime.now()
                    }
                    manutencoes_collection.insert_one(registro)
                    logger.info("Registro salvo no MongoDB")
                except Exception as db_error:
                    logger.error(f"Erro ao salvar no banco de dados: {db_error}")
            
            # Resetar estado para permitir novo diagnóstico
            user_state[user_id] = {'stage': 'intro'}
            
            # Mensagem final
            bot.send_message(message.chat.id, 
                "Posso ajudar em mais alguma coisa? "
                "Use /start para iniciar um novo diagnóstico.")
    
    except Exception as e:
        logger.error(f"Erro detalhado ao processar: {e}", exc_info=True)
        bot.reply_to(message, f"Desculpe, ocorreu um erro: {str(e)}")

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
    mongodb_ok = configurar_mongodb()
    
    if not (gemini_ok and mongodb_ok):
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
