#!/usr/bin/env python3
"""
Versão do controlador principal otimizada para servidor
Inclui health check server para serviços cloud
"""

import time
import schedule
import logging
from datetime import datetime, timedelta
import sys
import os
import signal
import threading
import json
from pathlib import Path
import hashlib

# Importar health server
from health_server import start_health_server, stop_health_server

# Importar o módulo principal (versão servidor)
try:
    from csgoroll_server import (
        executar_busca, criar_card_atleta, baixar_foto_atleta, 
        postar_no_x, criar_pastas, limpar_arquivos_atleta,
        obter_data_hoje, pegar_csrf_token, baixar_captcha, 
        ocr_captcha, tentar_busca, MAX_TENTATIVAS
    )
except ImportError:
    try:
        # Fallback para versão original
        from csgoroll_server import (
            executar_busca, criar_card_atleta, baixar_foto_atleta, 
            postar_no_x, criar_pastas, limpar_arquivos_atleta
        )
    except ImportError:
        print("Erro: Não foi possível importar o módulo csgoroll")
        sys.exit(1)

# Configuração do logging para servidor
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [SERVER] %(message)s',
    handlers=[
        logging.StreamHandler()  # Apenas console no servidor
    ]
)
logger = logging.getLogger(__name__)

class BIDMonitorServer:
    def __init__(self):
        self.running = False
        self.execucoes = 0
        self.ultima_execucao = None
        self.proxima_execucao = None
        self.arquivo_historico = "atletas_postados.json"
        self.thread_monitor = None
        self.ultimo_dia_verificado = None
        
    def limpar_historico_se_novo_dia(self):
        """Limpa o histórico se mudou o dia"""
        dia_atual = datetime.now().strftime('%Y-%m-%d')
        
        if self.ultimo_dia_verificado is None:
            self.ultimo_dia_verificado = dia_atual
            logger.info(f"Dia inicial definido: {dia_atual}")
            return False
        
        if dia_atual != self.ultimo_dia_verificado:
            logger.info(f"Mudança de dia detectada: {self.ultimo_dia_verificado} → {dia_atual}")
            logger.info("Limpando histórico de atletas postados (novo dia)")
            
            try:
                if Path(self.arquivo_historico).exists():
                    os.remove(self.arquivo_historico)
                    logger.info("Histórico limpo com sucesso!")
                else:
                    logger.info("Nenhum histórico para limpar")
                
                self.ultimo_dia_verificado = dia_atual
                return True
                
            except Exception as e:
                logger.error(f"Erro ao limpar histórico: {e}")
                return False
        
        return False
        
    def carregar_historico(self):
        """Carrega o histórico de atletas já postados"""
        try:
            if Path(self.arquivo_historico).exists():
                with open(self.arquivo_historico, 'r', encoding='utf-8') as f:
                    historico = json.load(f)
                logger.info(f"Histórico carregado: {len(historico)} atletas já postados")
                return historico
            else:
                logger.info("Nenhum histórico encontrado, iniciando do zero")
                return {}
        except Exception as e:
            logger.error(f"Erro ao carregar histórico: {e}")
            return {}
    
    def salvar_historico(self, historico):
        """Salva o histórico de atletas postados"""
        try:
            with open(self.arquivo_historico, 'w', encoding='utf-8') as f:
                json.dump(historico, f, ensure_ascii=False, indent=2)
            logger.info(f"Histórico salvo: {len(historico)} atletas")
        except Exception as e:
            logger.error(f"Erro ao salvar histórico: {e}")
    
    def gerar_hash_atleta(self, atleta_data):
        """Gera um hash único para identificar um atleta/contrato"""
        identificador = f"{atleta_data['codigo_atleta']}_{atleta_data['contrato_numero']}_{atleta_data['data_publicacao']}"
        return hashlib.md5(identificador.encode()).hexdigest()
    
    def buscar_e_processar_novos(self):
        """Executa busca e processa apenas atletas novos"""
        logger.info("Iniciando busca por novos contratos...")
        
        # Verificar se precisa limpar histórico (novo dia)
        self.limpar_historico_se_novo_dia()
        
        # Criar pastas na inicialização
        criar_pastas()
        
        # Carregar histórico
        historico = self.carregar_historico()
        
        data_busca = obter_data_hoje()
        logger.info(f"Buscando contratos para: {data_busca}")
        
        try:
            csrf_token = pegar_csrf_token()
            
            for tentativa in range(1, min(MAX_TENTATIVAS, 50) + 1):  # Limitar tentativas no servidor
                logger.info(f"Tentativa {tentativa}")
                try:
                    img_bytes = baixar_captcha()
                    captcha_text = ocr_captcha(img_bytes)
                    logger.info(f"OCR detectou: '{captcha_text}'")
                    
                    if len(captcha_text) < 3:
                        logger.warning("OCR falhou, texto muito curto. Pulando.")
                        continue
                    
                    resp = tentar_busca(csrf_token, captcha_text, data_busca)
                    
                    if "captcha" in resp.text.lower():
                        logger.error("CAPTCHA inválido")
                        time.sleep(1)  # Pausa menor no servidor
                        continue
                    else:
                        logger.info("CAPTCHA aceito!")
                        novos_postados = self.processar_resultados(resp, historico)
                        
                        if novos_postados > 0:
                            logger.info(f"{novos_postados} novos atletas processados!")
                        else:
                            logger.info("Nenhum atleta novo encontrado")
                        
                        return True
                        
                except Exception as e:
                    logger.error(f"Erro na tentativa {tentativa}: {e}")
                    continue
            
            logger.error("Máximo de tentativas atingido")
            return False
            
        except Exception as e:
            logger.error(f"Erro geral na busca: {e}")
            return False
    
    def processar_resultados(self, resp, historico):
        """Processa resultados e posta apenas atletas novos"""
        try:
            dados = resp.json()
            if not isinstance(dados, list) or len(dados) == 0:
                logger.info("Nenhum contrato encontrado no JSON.")
                return 0
            
            logger.info(f"{len(dados)} registro(s) encontrado(s)")
            
            novos_atletas = []
            for atleta in dados:
                hash_atleta = self.gerar_hash_atleta(atleta)
                
                if hash_atleta not in historico:
                    novos_atletas.append(atleta)
                    logger.info(f"Novo atleta encontrado: {atleta['nome']}")
                else:
                    logger.info(f"Atleta já postado: {atleta['nome']}")
            
            if not novos_atletas:
                logger.info("Todos os atletas já foram postados anteriormente")
                return 0
            
            # Processar apenas os novos
            postados_com_sucesso = 0
            for atleta in novos_atletas:
                foto_path = None
                card_path = None
                
                try:
                    logger.info(f"\nProcessando: {atleta['nome']}")
                    logger.info(f"Código: {atleta['codigo_atleta']}")
                    
                    # Baixar foto e criar card
                    foto_path = baixar_foto_atleta(atleta['codigo_atleta'], atleta['nome'])
                    card_path = criar_card_atleta(atleta, foto_path)
                    
                    if card_path:
                        logger.info("Postando no X...")
                        sucesso = postar_no_x(atleta, card_path)
                        
                        if sucesso:
                            # Adicionar ao histórico
                            hash_atleta = self.gerar_hash_atleta(atleta)
                            historico[hash_atleta] = {
                                'nome': atleta['nome'],
                                'codigo_atleta': atleta['codigo_atleta'],
                                'contrato_numero': atleta['contrato_numero'],
                                'data_publicacao': atleta['data_publicacao'],
                                'data_postagem': datetime.now().isoformat(),
                                'hash': hash_atleta
                            }
                            
                            postados_com_sucesso += 1
                            logger.info(f"{atleta['nome']} postado com sucesso!")
                        else:
                            logger.error(f"Falha ao postar {atleta['nome']}")
                    else:
                        logger.error(f"Falha ao criar card para {atleta['nome']}")
                    
                    # Salvar histórico após cada postagem bem-sucedida
                    if postados_com_sucesso > 0:
                        self.salvar_historico(historico)
                    
                except Exception as e:
                    logger.error(f"Erro ao processar {atleta.get('nome', 'atleta desconhecido')}: {e}")
                    continue
                
                finally:
                    # Limpeza de arquivos temporários
                    if foto_path or card_path:
                        logger.info("Limpando arquivos temporários...")
                        limpar_arquivos_atleta(foto_path, card_path)
                    
                    # Pausa menor entre processamentos no servidor
                    time.sleep(1)
            
            # Salvar histórico final
            self.salvar_historico(historico)
            
            logger.info(f"Resumo: {postados_com_sucesso}/{len(novos_atletas)} atletas postados com sucesso")
            return postados_com_sucesso
            
        except Exception as e:
            logger.error(f"Erro ao processar resultados: {e}")
            return 0
    
    def job_wrapper(self):
        """Wrapper que executa o job principal com tratamento de erros"""
        try:
            self.execucoes += 1
            self.ultima_execucao = datetime.now()
            
            logger.info(f"\n{'='*60}")
            logger.info(f"EXECUÇÃO #{self.execucoes} - {self.ultima_execucao.strftime('%d/%m/%Y %H:%M:%S')}")
            logger.info(f"{'='*60}")
            
            # Executar a busca com controle de duplicatas
            resultado = self.buscar_e_processar_novos()
            
            if resultado:
                logger.info("Busca executada com sucesso!")
            else:
                logger.warning("Busca executada, mas sem resultados ou com erros")
                
        except Exception as e:
            logger.error(f"Erro durante execução #{self.execucoes}: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Calcular próxima execução
        self.calcular_proxima_execucao()
        
        logger.info(f"Próxima execução em: {self.proxima_execucao.strftime('%d/%m/%Y %H:%M:%S')}")
        logger.info(f"{'='*60}\n")
    
    def calcular_proxima_execucao(self):
        """Calcula corretamente o horário da próxima execução"""
        try:
            jobs = schedule.get_jobs()
            if jobs:
                self.proxima_execucao = jobs[0].next_run
            else:
                self.proxima_execucao = datetime.now() + timedelta(minutes=10)
        except Exception as e:
            logger.error(f"Erro ao calcular próxima execução: {e}")
            self.proxima_execucao = datetime.now() + timedelta(minutes=10)
    
    def monitor_loop(self):
        """Loop principal do monitor em thread separada"""
        logger.info("Iniciando loop de monitoramento...")
        
        try:
            while self.running:
                schedule.run_pending()
                time.sleep(30)  # Verifica a cada 30 segundos
                
        except Exception as e:
            logger.error(f"Erro no loop de monitoramento: {e}")
            self.running = False
    
    def iniciar_monitoramento(self):
        """Inicia o monitoramento automático"""
        if self.running:
            logger.warning("Monitor já está em execução!")
            return
        
        logger.info("\nINICIANDO MONITORAMENTO AUTOMÁTICO DO BID CBF - SERVIDOR")
        logger.info("Execução programada a cada 10 minutos")
        logger.info("Health check server ativo na porta 8080")
        
        # Iniciar health check server
        start_health_server()
        
        # Limpar agendamentos anteriores
        schedule.clear()
        
        # Agendar execução a cada 10 minutos
        schedule.every(10).minutes.do(self.job_wrapper)
        
        # Executar imediatamente na primeira vez
        logger.info("Executando verificação inicial...")
        self.job_wrapper()
        
        self.running = True
        
        # Iniciar loop em thread separada
        self.thread_monitor = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread_monitor.start()
        
        try:
            # Loop principal para manter o programa vivo
            while self.running:
                time.sleep(60)  # Check a cada minuto no servidor
                
        except KeyboardInterrupt:
            logger.info("\nInterrupção solicitada pelo usuário")
            self.parar_monitoramento()
        except Exception as e:
            logger.error(f"Erro no controle principal: {e}")
            self.parar_monitoramento()
    
    def parar_monitoramento(self):
        """Para o monitoramento"""
        if not self.running:
            return
            
        logger.info("Parando monitoramento...")
        self.running = False
        schedule.clear()
        
        # Parar health server
        stop_health_server()
        
        if self.thread_monitor and self.thread_monitor.is_alive():
            logger.info("Aguardando thread finalizar...")
            self.thread_monitor.join(timeout=5)
        
        logger.info("Monitoramento finalizado")

def signal_handler(signum, frame):
    """Handler para sinais do sistema"""
    logger.info(f"\nSinal {signum} recebido, encerrando...")
    sys.exit(0)

def main():
    """Função principal otimizada para servidor"""
    logger.info("="*60)
    logger.info("MONITOR AUTOMÁTICO BID CBF - VERSÃO SERVIDOR")
    logger.info("   Otimizado para execução em containers/cloud")
    logger.info("   Health check endpoint: http://localhost:8080/health")
    logger.info("="*60)
    
    # Configurar handlers para sinais
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Criar e iniciar monitor
    monitor = BIDMonitorServer()
    
    try:
        # Modo automático direto (ideal para servidor)
        monitor.iniciar_monitoramento()
    except Exception as e:
        logger.error(f"Erro fatal: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()