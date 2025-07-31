import time
import requests
from bs4 import BeautifulSoup
import base64
from PIL import Image
from io import BytesIO
import pytesseract
import cv2
import numpy as np
import os
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from datetime import datetime
import logging
from webdriver_manager.chrome import ChromeDriverManager

# Configuração do logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bid_monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuração do Tesseract para servidor (automática)
# No servidor Linux, o tesseract geralmente está em /usr/bin/tesseract
try:
    # Tentar localização padrão do Linux
    if os.path.exists('/usr/bin/tesseract'):
        pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'
    # Fallback para outras localizações
    elif os.path.exists('/usr/local/bin/tesseract'):
        pytesseract.pytesseract.tesseract_cmd = '/usr/local/bin/tesseract'
    logger.info(f"Tesseract encontrado em: {pytesseract.pytesseract.tesseract_cmd}")
except Exception as e:
    logger.warning(f"Aviso na configuração do Tesseract: {e}")

# Parâmetros da consulta BID
UF = "SC"
CODIGO_CLUBE = "20019"
MAX_TENTATIVAS = 5000000000

# Credenciais do X (Twitter)
TWITTER_USERNAME = "biddocriciuma"
TWITTER_PASSWORD = "C@mpinh02134"

def criar_driver_chrome():
    """Cria driver do Chrome otimizado para servidor"""
    chrome_options = Options()
    
    # Opções essenciais para servidor
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-features=TranslateUI")
    chrome_options.add_argument("--disable-ipc-flooding-protection")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-logging")
    chrome_options.add_argument("--log-level=3")
    
    # Configurações para reduzir uso de memória
    chrome_options.add_argument("--memory-pressure-off")
    chrome_options.add_argument("--max_old_space_size=4096")
    
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    try:
        # Usar WebDriverManager para gerenciar ChromeDriver automaticamente
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    except Exception as e:
        logger.error(f"Erro ao criar driver Chrome: {e}")
        # Fallback: tentar sem WebDriverManager
        try:
            driver = webdriver.Chrome(options=chrome_options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            return driver
        except Exception as e2:
            logger.error(f"Erro no fallback do Chrome: {e2}")
            raise

def limpar_nome_arquivo(nome):
    """Remove caracteres inválidos para nomes de arquivo"""
    nome_limpo = re.sub(r'[<>:"/\\|?*]', '_', nome)
    nome_limpo = nome_limpo.replace(' ', '_')
    return nome_limpo

def criar_pastas():
    """Cria as pastas necessárias se não existirem"""
    pastas = ["fotos_atletas", "cards_atletas"]
    
    for pasta in pastas:
        if not os.path.exists(pasta):
            os.makedirs(pasta)
            logger.info(f"Pasta criada: {pasta}")
        else:
            logger.info(f"Pasta já existe: {pasta}")

def limpar_arquivo(caminho_arquivo):
    """Remove um arquivo específico se ele existir"""
    try:
        if caminho_arquivo and os.path.exists(caminho_arquivo):
            os.remove(caminho_arquivo)
            logger.info(f"Arquivo removido: {caminho_arquivo}")
            return True
        return False
    except Exception as e:
        logger.error(f"Erro ao remover arquivo {caminho_arquivo}: {e}")
        return False

def limpar_arquivos_atleta(foto_path, card_path):
    """Remove os arquivos temporários de foto e card do atleta"""
    arquivos_removidos = 0
    
    if limpar_arquivo(foto_path):
        arquivos_removidos += 1
    
    if limpar_arquivo(card_path):
        arquivos_removidos += 1
    
    if arquivos_removidos > 0:
        logger.info(f"Limpeza concluída: {arquivos_removidos} arquivo(s) removido(s)")
    
    return arquivos_removidos

# Sessão HTTP
session = requests.Session()

def obter_data_hoje():
    """Retorna a data atual no formato DD/MM/YYYY"""
    return datetime.now().strftime("%d/%m/%Y")

def pegar_csrf_token():
    """Captura o token CSRF da página"""
    try:
        resp = session.get("https://bid.cbf.com.br", timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
        token = soup.find("meta", attrs={"name": "csrf-token"})["content"]
        logger.info("Token CSRF capturado")
        return token
    except Exception as e:
        logger.error(f"Erro ao capturar token CSRF: {e}")
        raise

def baixar_captcha():
    """Baixa a imagem do CAPTCHA"""
    try:
        resp = session.get("https://bid.cbf.com.br/get-captcha-base64", timeout=30)
        img_data = base64.b64decode(resp.text)
        return img_data
    except Exception as e:
        logger.error(f"Erro ao baixar CAPTCHA: {e}")
        raise

def ocr_captcha(imagem_bytes):
    """Processa o CAPTCHA usando OCR"""
    try:
        img = Image.open(BytesIO(imagem_bytes)).convert("RGB")
        img_np = np.array(img)
        gray = cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY)
        gray = cv2.medianBlur(gray, 3)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
        config = r'--psm 7 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        texto = pytesseract.image_to_string(thresh, config=config)
        return texto.strip()
    except Exception as e:
        logger.error(f"Erro no OCR do CAPTCHA: {e}")
        return ""

def baixar_foto_atleta(codigo_atleta, nome_atleta):
    """Baixa a foto do atleta do BID"""
    try:
        url_foto = f"https://bid.cbf.com.br/foto-atleta/{codigo_atleta}"
        logger.info(f"Baixando foto de {nome_atleta}...")
        resp = session.get(url_foto, timeout=30)
        if resp.status_code == 200:
            try:
                nome_limpo = limpar_nome_arquivo(nome_atleta)
                nome_arquivo = f"{codigo_atleta}_{nome_limpo}.jpg"
                caminho_arquivo = os.path.join("fotos_atletas", nome_arquivo)
                with open(caminho_arquivo, "wb") as f:
                    f.write(resp.content)
                logger.info(f"Foto salva: {caminho_arquivo}")
                return caminho_arquivo
            except Exception as e:
                logger.error(f"Erro ao processar imagem de {nome_atleta}: {e}")
        else:
            logger.warning(f"Foto não disponível para {nome_atleta}: status {resp.status_code}")
    except Exception as e:
        logger.error(f"Erro ao baixar foto de {nome_atleta}: {e}")
    return None

def imagem_para_base64(caminho_imagem):
    """Converte imagem para base64"""
    try:
        with open(caminho_imagem, "rb") as f:
            img_data = f.read()
        img_base64 = base64.b64encode(img_data).decode()
        return f"data:image/jpeg;base64,{img_base64}"
    except Exception as e:
        logger.error(f"Erro ao converter imagem para base64: {e}")
        return ""

def criar_card_atleta(atleta_data, foto_path):
    """Cria um card visual do atleta"""
    driver = None
    try:
        foto_src = ""
        if foto_path and os.path.exists(foto_path):
            foto_src = imagem_para_base64(foto_path)
        else:
            foto_src = f"https://bid.cbf.com.br/foto-atleta/{atleta_data['codigo_atleta']}"
        
        # Formatação da data de término
        data_termino_formatada = None
        if 'datatermino' in atleta_data and atleta_data['datatermino']:
            try:
                ano, mes, dia = atleta_data['datatermino'].split("-")
                data_termino_formatada = f"{dia}/{mes}/{ano}"
            except:
                data_termino_formatada = atleta_data['datatermino']
        
        termino_linha = ""
        if data_termino_formatada:
            termino_linha = f'<p><span class="label">Término:</span> <span class="value">{data_termino_formatada}</span></p>'
        
        css_estilo = """
        <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f8f9fa; padding: 20px; min-height: 100vh; }
        .container { width: 100%; max-width: 700px; background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); margin: 0 auto; min-height: 400px; }
        .atleta-nome { font-size: 1.5rem; font-weight: bold; color: #0d6efd; margin-bottom: 20px; text-align: center; border-bottom: 2px solid #0d6efd; padding-bottom: 10px; }
        .content-wrapper { display: flex; gap: 25px; align-items: flex-start; width: 100%; }
        .foto-section { flex: 0 0 220px; text-align: center; }
        .info-section { flex: 1; min-width: 0; }
        .foto-atleta { width: 200px; height: 250px; object-fit: cover; border-radius: 12px; border: 3px solid #e9ecef; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        .atleta-info { margin-bottom: 20px; }
        .atleta-info p { margin: 10px 0; font-size: 1rem; line-height: 1.5; display: flex; align-items: center; }
        .atleta-info .label { min-width: 130px; font-weight: normal; color: #495057; }
        .atleta-info .value { font-weight: bold; color: #212529; flex: 1; }
        .clube-section { padding-top: 15px; border-top: 2px solid #dee2e6; display: flex; justify-content: space-between; align-items: center; }
        .clube-info { display: flex; align-items: center; color: #6c757d; font-size: 1rem; }
        .escudo-clube { width: 40px; height: 40px; margin-right: 12px; border-radius: 6px; object-fit: contain; }
        .btn-historico { padding: 10px 20px; border: 2px solid #0d6efd; color: #0d6efd; text-decoration: none; border-radius: 6px; background: transparent; font-weight: 500; font-size: 0.9rem; white-space: nowrap; }
        </style>
        """
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            {css_estilo}
        </head>
        <body>
            <div class="container">
                <div class="atleta-nome">{atleta_data['nome']}</div>
                <div class="content-wrapper">
                    <div class="foto-section">
                        <img alt="{atleta_data['nome']}" class="foto-atleta" src="{foto_src}">
                    </div>
                    <div class="info-section">
                        <div class="atleta-info">
                            <p><span class="label">Nº de Contrato:</span> <span class="value">{atleta_data['contrato_numero']}</span></p>
                            <p><span class="label">Tipo Contrato:</span> <span class="value">{atleta_data['tipocontrato']}</span></p>
                            <p><span class="label">Publicação:</span> <span class="value">{atleta_data['data_publicacao']}</span></p>
                            {termino_linha}
                            <p><span class="label">Inscrição:</span> <span class="value">{atleta_data['codigo_atleta']}</span></p>
                            <p><span class="label">Apelido:</span> <span class="value">{atleta_data.get('apelido', '-')}</span></p>
                            <p><span class="label">Nascimento:</span> <span class="value">{atleta_data['data_nascimento']}</span></p>
                        </div>
                        <div class="clube-section">
                            <div class="clube-info">
                                <img alt="Escudo" class="escudo-clube"
                                     src="https://bid.cbf.com.br/files/clubes/{CODIGO_CLUBE}/escudo.jpg"
                                     onerror="this.style.display='none'">
                                <span>{atleta_data['clube']} - SC</span>
                            </div>
                            <a href="https://bid.cbf.com.br/atleta-competicoes/{atleta_data['codigo_atleta']}"
                               class="btn-historico">VER HISTÓRICO</a>
                        </div>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        driver = criar_driver_chrome()
        driver.set_window_size(900, 700)
        driver.execute_script("document.open(); document.write(arguments[0]); document.close();", html_content)
        time.sleep(3)
        
        nome_limpo = limpar_nome_arquivo(atleta_data['nome'])
        nome_arquivo = f"{atleta_data['codigo_atleta']}_{nome_limpo}_card.png"
        caminho_card = os.path.join("cards_atletas", nome_arquivo)
        
        element = driver.find_element(By.CSS_SELECTOR, ".container")
        element.screenshot(caminho_card)
        logger.info(f"Card criado: {caminho_card}")
        return caminho_card
        
    except Exception as e:
        logger.error(f"Erro ao criar card: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def postar_no_x(atleta_data, card_path):
    """Posta o atleta no X (Twitter)"""
    driver = None
    try:
        driver = criar_driver_chrome()
        
        # Formatação da data de publicação
        data_publicacao = atleta_data['data_publicacao']
        if " " in data_publicacao and "-" in data_publicacao:
            try:
                data_parte, hora_parte = data_publicacao.split(" ")
                hora_parte = hora_parte.split(".")[0]
                ano, mes, dia = data_parte.split("-")
                data_formatada = f"{dia}/{mes}/{ano} {hora_parte}"
            except:
                data_formatada = data_publicacao
        else:
            data_formatada = data_publicacao
        
        # Formatação da data de término
        data_termino_tweet = None
        if 'datatermino' in atleta_data and atleta_data['datatermino']:
            try:
                ano, mes, dia = atleta_data['datatermino'].split("-")
                data_termino_tweet = f"{dia}/{mes}/{ano}"
            except:
                data_termino_tweet = atleta_data['datatermino']
        
        # Linha de término opcional
        if data_termino_tweet:
            linha_termino = f"Data de término do contrato: {data_termino_tweet}"
        else:
            linha_termino = ""
        
        # Preparar hashtags
        nome_limpo = atleta_data['nome'].replace(' ', '')
        clube_tag = atleta_data['clube'].replace(' ', '')
        
        # Novo formato do tweet com linhas em branco, alinhamento correto e hashtags
        tweet_texto = (
            f"Jogador publicado no BID: {atleta_data['nome']}\n\n"
            f"Publicado em: {data_formatada}\n\n"
            f"Tipo de contrato: {atleta_data['tipocontrato']}"
        )
        if linha_termino:
            tweet_texto += f"\n\n{linha_termino}"
        tweet_texto += f"\n\n#{nome_limpo} #BID #{clube_tag}"
        
        logger.info("Fazendo login no X...")
        driver.get("https://x.com/login")
        time.sleep(5)
        
        # Login
        username_input = driver.find_element(By.NAME, "text")
        username_input.clear()
        username_input.send_keys(TWITTER_USERNAME)
        username_input.send_keys(Keys.RETURN)
        time.sleep(3)
        
        password_input = driver.find_element(By.NAME, "password")
        password_input.clear()
        password_input.send_keys(TWITTER_PASSWORD)
        password_input.send_keys(Keys.RETURN)
        time.sleep(8)
        
        logger.info("Login realizado com sucesso!")
        
        # Navegar para home
        driver.get("https://x.com/home")
        time.sleep(5)
        
        # Abrir composição
        logger.info("Abrindo composição de tweet...")
        tweet_button = None
        for selector in [
            '//a[@data-testid="SideNav_NewTweet_Button"]',
            '//div[@data-testid="SideNav_NewTweet_Button"]',
            '//span[text()="Tweet"]//ancestor::a',
            '//span[text()="Post"]//ancestor::a',
            '//div[@aria-label="Post"]'
        ]:
            try:
                tweet_button = driver.find_element(By.XPATH, selector)
                break
            except:
                continue
        
        if not tweet_button:
            logger.error("Não foi possível encontrar o botão de tweet")
            return False
        
        tweet_button.click()
        time.sleep(4)
        logger.info("Composição aberta!")
        
        # Encontrar campo de texto
        tweet_textarea = None
        for selector in [
            '//div[@aria-label="Tweet text" and @role="textbox"]',
            '//div[@data-testid="tweetTextarea_0"]',
            '//div[@role="dialog"]//div[@aria-label="Tweet text"]',
            '//div[@role="dialog"]//div[contains(@class, "public-DraftStyleDefault-block")]'
        ]:
            try:
                tweet_textarea = driver.find_element(By.XPATH, selector)
                break
            except:
                continue
        
        if not tweet_textarea:
            logger.error("Não foi possível encontrar o campo de texto")
            return False
        
        # Escrever texto
        driver.execute_script("arguments[0].focus();", tweet_textarea)
        driver.execute_script("arguments[0].click();", tweet_textarea)
        time.sleep(1)
        
        logger.info("Digitando texto...")
        tweet_textarea.send_keys(tweet_texto)
        time.sleep(2)
        
        # Upload da imagem
        if card_path and os.path.exists(card_path):
            logger.info("Fazendo upload da imagem...")
            try:
                # Tentar encontrar input de arquivo
                upload_input = driver.find_element(By.XPATH, '//div[@role="dialog"]//input[@type="file"]')
                upload_input.send_keys(os.path.abspath(card_path))
                
                # Aguardar upload
                logger.info("Aguardando upload...")
                upload_concluido = False
                for i in range(15):
                    try:
                        driver.find_element(By.XPATH, '//div[@role="dialog"]//div[@data-testid="attachments"]//img')
                        upload_concluido = True
                        logger.info("Upload concluído!")
                        break
                    except:
                        time.sleep(1)
                
                if not upload_concluido:
                    logger.warning("Upload pode não ter terminado")
                else:
                    time.sleep(2)
                    
            except Exception as e:
                logger.error(f"Erro ao fazer upload: {e}")
        
        # Postar
        logger.info("Postando tweet...")
        time.sleep(2)
        
        post_button = None
        # Lista de seletores para o botão de postar
        selectors = [
            '/html/body/div[1]/div/div/div[1]/div[2]/div/div/div/div/div/div[2]/div[2]/div/div/div/div[3]/div[2]/div[1]/div/div/div/div[2]/div[2]/div/div/div/button[2]',
            '//div[@data-testid="tweetButtonInline"]',
            '//span[text()="Post"]//ancestor::button',
            '//span[text()="Tweet"]//ancestor::button',
            '//div[@role="dialog"]//button[contains(@data-testid, "tweetButton")]'
        ]
        
        for selector in selectors:
            try:
                post_button = driver.find_element(By.XPATH, selector)
                if post_button.is_enabled():
                    logger.info("Botão de postar encontrado")
                    driver.execute_script("arguments[0].scrollIntoView(true);", post_button)
                    time.sleep(1)
                    driver.execute_script("arguments[0].click();", post_button)
                    time.sleep(8)
                    logger.info("Tweet postado com sucesso!")
                    return True
                else:
                    logger.warning("Botão encontrado mas desabilitado")
                    continue
            except Exception as e:
                logger.debug(f"Tentativa com selector falhou: {selector}")
                continue
        
        logger.error("Não foi possível encontrar/clicar no botão de postar")
        return False
        
    except Exception as e:
        logger.error(f"Erro geral ao postar no X: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def tentar_busca(token, captcha, data_busca):
    """Faz a requisição de busca no BID"""
    headers = {
        "X-CSRF-TOKEN": token,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    payload = {
        "data": data_busca,
        "uf": UF,
        "codigo_clube": CODIGO_CLUBE,
        "captcha": captcha
    }
    
    try:
        resp = session.post("https://bid.cbf.com.br/busca-json", data=payload, headers=headers, timeout=30)
        return resp
    except Exception as e:
        logger.error(f"Erro na requisição de busca: {e}")
        raise

def exibir_resultados(resp, data_busca):
    """Exibe e processa os resultados encontrados"""
    try:
        dados = resp.json()
        if isinstance(dados, list) and len(dados) > 0:
            logger.info(f"\n{len(dados)} registro(s) encontrado(s) para {data_busca}:\n")
            
            for atleta in dados:
                foto_path = None
                card_path = None
                
                try:
                    logger.info(f" {atleta['nome']} (Apelido: {atleta.get('apelido', '-')})")
                    logger.info(f" Código do Atleta: {atleta['codigo_atleta']}")
                    logger.info(f" Contrato: {atleta['contrato_numero']}")
                    
                    # Baixar foto e criar card
                    foto_path = baixar_foto_atleta(atleta['codigo_atleta'], atleta['nome'])
                    card_path = criar_card_atleta(atleta, foto_path)
                    
                    if card_path:
                        logger.info("Postando no X...")
                        sucesso = postar_no_x(atleta, card_path)
                        if sucesso:
                            logger.info(f"Post criado para {atleta['nome']}")
                        else:
                            logger.error(f"Falha ao postar {atleta['nome']}")
                    else:
                        logger.error(f"Falha ao criar card para {atleta['nome']}")
                    
                    logger.info("\n" + "-"*50 + "\n")
                    
                except Exception as e:
                    logger.error(f"Erro ao processar atleta {atleta.get('nome', 'desconhecido')}: {e}")
                
                finally:
                    # LIMPEZA: Remove os arquivos temporários após o processamento
                    if foto_path or card_path:
                        logger.info("Iniciando limpeza de arquivos temporários...")
                        arquivos_removidos = limpar_arquivos_atleta(foto_path, card_path)
                        logger.info(f"Limpeza concluída para {atleta.get('nome', 'atleta')}")
                    
                    # Pausa entre processamentos
                    time.sleep(3)
                
            return True
        else:
            logger.info("Nenhum contrato encontrado no JSON.")
            return False
    except Exception as e:
        logger.error(f"Erro ao processar JSON: {e}")
        return False

def executar_busca():
    """Função principal que executa uma busca completa"""
    logger.info("Iniciando execução do monitor BID...")
    
    # IMPORTANTE: Criar pastas na inicialização
    criar_pastas()
    
    data_busca = obter_data_hoje()
    logger.info(f"Buscando contratos para: {data_busca}")
    
    try:
        logger.info("Capturando CSRF token...")
        csrf_token = pegar_csrf_token()
        
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            logger.info(f"\nTentativa {tentativa}/{MAX_TENTATIVAS}")
            try:
                img_bytes = baixar_captcha()
                captcha_text = ocr_captcha(img_bytes)
                logger.info(f"OCR detectou: '{captcha_text}'")
                
                if len(captcha_text) < 3:
                    logger.warning("OCR falhou, texto muito curto. Pulando.")
                    continue
                
                resp = tentar_busca(csrf_token, captcha_text, data_busca)
                
                if "captcha" in resp.text.lower() or "inválido" in resp.text.lower():
                    logger.warning("CAPTCHA inválido, tentando novamente...")
                    time.sleep(2)  # Pausa antes da próxima tentativa
                else:
                    logger.info("CAPTCHA aceito!")
                    resultado = exibir_resultados(resp, data_busca)
                    logger.info("Execução concluída!")
                    return resultado
                    
            except Exception as e:
                logger.error(f"Erro na tentativa {tentativa}: {e}")
                time.sleep(2)
        
        logger.error(f"Máximo de tentativas ({MAX_TENTATIVAS}) atingido")
        return False
        
    except Exception as e:
        logger.error(f"Erro geral na execução: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return False

if __name__ == "__main__":
    executar_busca()