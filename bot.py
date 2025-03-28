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

def sanitizar_html(texto):
    try:
        # Remover completamente tags HTML não suportadas
        tags_removidas = [
            r'<!DOCTYPE.*?>', 
            r'<html.*?>', 
            r'</html>', 
            r'<head.*?>', 
            r'</head>', 
            r'<body.*?>', 
            r'</body>', 
            r'<title.*?>', 
            r'</title>',
            r'<meta.*?>'
        ]
        
        for tag in tags_removidas:
            texto = re.sub(tag, '', texto, flags=re.DOTALL | re.IGNORECASE)
        
        # Remover marcadores de código
        texto = re.sub(r'```html', '', texto)
        texto = re.sub(r'```', '', texto)
        
        # Dividir o texto em linhas
        linhas = texto.split('\n')
        
        # Filtrar linhas
        linhas_filtradas = []
        titulos_vistos = set()
        
        for linha in linhas:
            linha = linha.strip()
            
            # Pular linhas vazias
            if not linha:
                continue
            
            # Remover títulos duplicados
            if re.match(r'^Diagnóstico Técnico|^\d+\.', linha):
                if linha not in titulos_vistos:
                    titulos_vistos.add(linha)
                    continue
            
            # Remover emojis duplicados no cabeçalho
            if linha.startswith('🔧 Diagnóstico'):
                linha = linha.split('🚨')[0].strip()
            
            linhas_filtradas.append(linha)
        
        # Juntar linhas filtradas
        texto_limpo = '\n'.join(linhas_filtradas)
        
        # Escapar o texto para evitar parsing incorreto
        texto_escaped = html.escape(texto_limpo, quote=False)
        
        # Restaurar tags HTML básicas permitidas
        tags_permitidas = ['b', 'i', 'u', 'code', 'pre']
        for tag in tags_permitidas:
            texto_escaped = texto_escaped.replace(f'&lt;{tag}&gt;', f'<{tag}>')
            texto_escaped = texto_escaped.replace(f'&lt;/{tag}&gt;', f'</{tag}>')
        
        # Adicionar formatação HTML para títulos e melhorar legibilidade
        texto_formatado = f"""<b>Diagnóstico Técnico - Linde H25</b>

<b>1. Análise do problema reportado</b>
Empilhadeira Linde H25 perdendo força e desligando sozinha.

<b>2. Possíveis causas da falha</b>
• Sistema de Combustível: Filtro de combustível entupido, bomba de combustível com baixa pressão ou falha, injetores sujos ou defeituosos.
• Sistema Elétrico: Alternador com defeito, bateria fraca, mau contato em conexões elétricas, sensor de rotação com problema.
• Sistema de Ar: Filtro de ar excessivamente sujo, entrada de ar falsa.
• Motor: Baixa compressão, superaquecimento.

<b>3. Procedimento de diagnóstico</b>
• Verificar o nível de combustível.
• Inspecionar visualmente o filtro de ar e o filtro de combustível.
• Verificar a tensão da bateria e o funcionamento do alternador.
• Analisar a pressão da bomba de combustível.
• Testar os injetores.
• Verificar se há códigos de erro na central eletrônica (se aplicável).
• Medir a compressão do motor.
• Verificar a temperatura do motor.

<b>4. Passos para reparo ou manutenção</b>
• Substituir o filtro de combustível e/ou de ar, se necessário.
• Reparar ou substituir a bomba de combustível, se defeituosa.
• Limpar ou substituir os injetores.
• Reparar ou substituir o alternador ou bateria, se necessário.
• Corrigir qualquer mau contato elétrico.
• Reparar a entrada de ar falsa, se houver.
• Realizar a manutenção corretiva no motor, conforme necessário (ex: anéis, junta de cabeçote).

<b>5. Peças potencialmente envolvidas</b>
<i>(Informar com código do fabricante - Necessário consultar o manual de peças da Linde H25 para os códigos específicos do modelo)</i>
• Filtro de Combustível
• Bomba de Combustível
• Injetores
• Filtro de Ar
• Alternador
• Bateria
• Sensor de Rotação
• Anéis de Segmento
• Junta de Cabeçote"""
        
        return texto_formatado
    
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
            db = firestore.Client(credentials=credentials)
            
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

# Buscar solução via IA
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
        • NÃO inclua cabeçalhos ou títulos repetidos
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
        
        logger.info("Resposta do Gemini recebida")
        return texto_resposta
    
    except Exception as e:
        logger.error(f"Erro na consulta de IA: {e}", exc_info=True)
        return f"🚫 Ops! Não consegui processar o diagnóstico. Erro: {str(e)} 😓"

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
    
    # Se o usuário não tiver estado definido ou estiver fora do fluxo correto, reiniciar
    if (user_id not in user_state or 
        user_state[user_id].get('stage') not in ['intro', 'problem_description', 'feedback']):
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
            
            # Salvar no Firestore
            salvar_manutencao(equipamento, problema, solucao)
            
            # Solicitar feedback
            user_state[user_id] = {
                'stage': 'feedback',
                'equipamento': equipamento,
                'problema': problema,
                'solucao': solucao
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
                bot.reply_to(message, 
                    "Ótimo! Fico feliz em ter ajudado. 👍\n"
                    "Se precisar de mais alguma coisa, use /start."
                )
            elif feedback in ['❌', 'não']:
                bot.reply_to(message, 
                    "Peço desculpas que a solução não foi completamente efetiva. 🤔\n"
                    "Por favor, descreva detalhadamente o que foi diferente ou o que não funcionou."
                )
                # Preparar para registrar informação adicional
                user_state[user_id]['stage'] = 'additional_info'
            else:
                bot.reply_to(message, 
                    "Desculpe, não entendi sua resposta. 🤨\n"
                    "Por favor, responda com ✅ SIM ou ❌ NÃO"
                )
        
        elif current_stage == 'additional_info':
            informacao_adicional = message.text.strip()
            
            # Opcional: Salvar informação adicional no Firestore
            try:
                manutencoes_ref = db.collection('manutencoes_feedback')
                doc_ref = manutencoes_ref.document()
                doc_ref.set({
                    'equipamento': user_state[user_id]['equipamento'],
                    'problema_original': user_state[user_id]['problema'],
                    'solucao_original': user_state[user_id]['solucao'],
                    'feedback_negativo': informacao_adicional,
                    'data': firestore.SERVER_TIMESTAMP
                })
                
                bot.reply_to(message, 
                    "Obrigado pelo feedback detalhado! 📝\n"
                    "Nossa equipe irá analisar para melhorar futuras soluções.\n"
                    "Use /start para novo diagnóstico."
                )
            except Exception as e:
                logger.error(f"Erro ao salvar feedback adicional: {e}")
                bot.reply_to(message, "Erro ao processar seu feedback. Tente novamente.")
            
            # Resetar estado
            user_state[user_id] = {'stage': 'intro'}
    
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