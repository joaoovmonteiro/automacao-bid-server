#!/usr/bin/env python3
"""
Controlador principal para monitoramento automático do BID CBF
Executa verificações a cada 30 minutos com controle de duplicatas
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

# Importar o módulo principal
try:
    from csgoroll import executar_busca, criar_card_atleta, baixar_foto_atleta, postar_no_x, criar_pastas, limpar_arquivos_atleta
except ImportError:
    print("Erro: Não foi possível importar o módulo csgoroll.py")
    print("   Certifique-se de que o arquivo csgoroll.py está no mesmo diretório")
    sys.exit(1)

# Configuração do logging para o controlador
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [CONTROLADOR] %(message)s',
    handlers=[
        logging.FileHandler('controlador.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BIDMonitor:
    def __init__(self):
        self.running = False
        self.execucoes = 0
        self.ultima_execucao = None
        self.proxima_execucao = None
        self.arquivo_historico = "atletas_postados.json"
        self.thread_monitor = None
        self.ultimo_dia_verificado = None  # Para controlar limpeza diária
        
    def limpar_historico_se_novo_dia(self):
        """Limpa o histórico se mudou o dia"""
        dia_atual = datetime.now().strftime('%Y-%m-%d')
        
        if self.ultimo_dia_verificado is None:
            # Primeira execução, definir o dia atual
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
        # Usar código do atleta + número do contrato + data de publicação
        identificador = f"{atleta_data['codigo_atleta']}_{atleta_data['contrato_numero']}_{atleta_data['data_publicacao']}"
        return hashlib.md5(identificador.encode()).hexdigest()
    
    def buscar_e_processar_novos(self):
        """Executa busca e processa apenas atletas novos"""
        from csgoroll import (
            obter_data_hoje, pegar_csrf_token, baixar_captcha, 
            ocr_captcha, tentar_busca, MAX_TENTATIVAS
        )
        
        logger.info("Iniciando busca por novos contratos...")
        
        # IMPORTANTE: Verificar se precisa limpar histórico (novo dia)
        self.limpar_historico_se_novo_dia()
        
        # IMPORTANTE: Criar pastas na inicialização
        criar_pastas()
        
        # Carregar histórico
        historico = self.carregar_historico()
        
        data_busca = obter_data_hoje()
        logger.info(f"Buscando contratos para: {data_busca}")
        
        try:
            csrf_token = pegar_csrf_token()
            
            for tentativa in range(1, MAX_TENTATIVAS + 1):
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
                    # LIMPEZA: Remove os arquivos temporários após o processamento
                    if foto_path or card_path:
                        logger.info("Iniciando limpeza de arquivos temporários...")
                        arquivos_removidos = limpar_arquivos_atleta(foto_path, card_path)
                        logger.info(f"Limpeza concluída para {atleta.get('nome', 'atleta')}")
                    
                    # Pausa entre processamentos
                    time.sleep(2)
            
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
        
        # Calcular próxima execução CORRETAMENTE
        self.calcular_proxima_execucao()
        
        logger.info(f"Próxima execução em: {self.proxima_execucao.strftime('%d/%m/%Y %H:%M:%S')}")
        logger.info(f"{'='*60}\n")
    
    def calcular_proxima_execucao(self):
        """Calcula corretamente o horário da próxima execução"""
        try:
            # Pegar o próximo job agendado
            jobs = schedule.get_jobs()
            if jobs:
                # Usar o next_run do primeiro job
                self.proxima_execucao = jobs[0].next_run
            else:
                # Fallback: calcular manualmente (10 minutos a partir de agora)
                self.proxima_execucao = datetime.now() + timedelta(minutes=10)
        except Exception as e:
            logger.error(f"Erro ao calcular próxima execução: {e}")
            # Fallback em caso de erro
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
        
        logger.info("\nINICIANDO MONITORAMENTO AUTOMÁTICO DO BID CBF")
        logger.info("Execução programada a cada 10 minutos")
        logger.info("Limpeza automática do histórico todo dia às 00:00")
        logger.info("Para parar, pressione Ctrl+C\n")
        
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
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("\nInterrupção solicitada pelo usuário (Ctrl+C)")
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
        
        if self.thread_monitor and self.thread_monitor.is_alive():
            logger.info("Aguardando thread finalizar...")
            self.thread_monitor.join(timeout=5)
        
        logger.info("Monitoramento finalizado")
    
    def status(self):
        """Retorna status atual do monitoramento"""
        return {
            'running': self.running,
            'execucoes': self.execucoes,
            'ultima_execucao': self.ultima_execucao,
            'proxima_execucao': self.proxima_execucao,
            'ultimo_dia_verificado': self.ultimo_dia_verificado
        }
    
    def exibir_status(self):
        """Exibe status formatado"""
        print("\n" + "="*50)
        print("STATUS DO MONITOR BID CBF")
        print("="*50)
        print(f"Status: {'ATIVO' if self.running else 'INATIVO'}")
        print(f"Execuções realizadas: {self.execucoes}")
        
        if self.ultima_execucao:
            print(f"Última execução: {self.ultima_execucao.strftime('%d/%m/%Y %H:%M:%S')}")
        else:
            print("Última execução: Nunca")
            
        if self.proxima_execucao and self.running:
            print(f"Próxima execução: {self.proxima_execucao.strftime('%d/%m/%Y %H:%M:%S')}")
        else:
            print("Próxima execução: Não agendada")
        
        if self.ultimo_dia_verificado:
            print(f"Último dia verificado: {self.ultimo_dia_verificado}")
        
        # Mostrar estatísticas do histórico
        try:
            historico = self.carregar_historico()
            print(f"Total de atletas postados hoje: {len(historico)}")
        except:
            print("Total de atletas postados hoje: Erro ao carregar")
            
        print("="*50)
    
    def executar_unica_vez(self):
        """Executa uma verificação única"""
        logger.info("Executando verificação única...")
        try:
            resultado = self.buscar_e_processar_novos()
            if resultado:
                logger.info("Verificação única concluída com sucesso!")
                return True
            else:
                logger.warning("Verificação única concluída sem resultados")
                return False
        except Exception as e:
            logger.error(f"Erro durante verificação única: {e}")
            return False

def signal_handler(signum, frame):
    """Handler para sinais do sistema (Ctrl+C, etc.)"""
    logger.info("\nSinal de interrupção recebido")
    sys.exit(0)

def verificar_dependencias():
    """Verifica se todas as dependências estão instaladas"""
    dependencias = {
        'requests': 'requests',
        'beautifulsoup4': 'bs4',
        'pillow': 'PIL',
        'pytesseract': 'pytesseract',
        'opencv-python': 'cv2',
        'numpy': 'numpy',
        'selenium': 'selenium',
        'schedule': 'schedule'
    }
    
    logger.info("Verificando dependências...")
    faltando = []
    
    for nome_pip, nome_import in dependencias.items():
        try:
            __import__(nome_import)
            logger.info(f"   ✓ {nome_pip}")
        except ImportError:
            faltando.append(nome_pip)
            logger.error(f"   ✗ {nome_pip}")
    
    if faltando:
        logger.error("Dependências faltando:")
        for dep in faltando:
            logger.error(f"   - {dep}")
        logger.error("   Execute: pip install " + " ".join(faltando))
        return False
    
    logger.info("Todas as dependências estão instaladas ✓")
    return True

def verificar_arquivos():
    """Verifica se os arquivos necessários existem"""
    arquivos_necessarios = ['csgoroll.py']
    
    logger.info("Verificando arquivos necessários...")
    for arquivo in arquivos_necessarios:
        if not Path(arquivo).exists():
            logger.error(f"Arquivo não encontrado: {arquivo}")
            return False
        logger.info(f"   ✓ {arquivo}")
    
    logger.info("Todos os arquivos necessários estão presentes ✓")
    return True

def menu_interativo():
    """Menu interativo para controle do monitor"""
    monitor = BIDMonitor()
    
    while True:
        print("\n" + "="*60)
        print("MONITOR BID CBF - MENU PRINCIPAL")
        print("="*60)
        print("1. Iniciar monitoramento automático (10min)")
        print("2. Executar uma verificação única")
        print("3. Exibir status e estatísticas")
        print("4. Ver histórico de atletas postados")
        print("5. Limpar histórico")
        print("6. Sair")
        print("="*60)
        
        try:
            opcao = input("Escolha uma opção (1-6): ").strip()
            
            if opcao == '1':
                if monitor.running:
                    print("Monitor já está rodando!")
                    continue
                
                print("\nIniciando monitoramento automático...")
                print("   Para parar, pressione Ctrl+C")
                
                try:
                    monitor.iniciar_monitoramento()
                except KeyboardInterrupt:
                    print("\nMonitoramento interrompido")
                    monitor.parar_monitoramento()
                    
            elif opcao == '2':
                print("\nExecutando verificação única...")
                resultado = monitor.executar_unica_vez()
                if resultado:
                    print("Verificação concluída!")
                else:
                    print("Verificação sem resultados")
                input("\nPressione Enter para continuar...")
                
            elif opcao == '3':
                monitor.exibir_status()
                input("\nPressione Enter para continuar...")
                
            elif opcao == '4':
                print("\nHISTÓRICO DE ATLETAS POSTADOS HOJE")
                print("="*50)
                try:
                    historico = monitor.carregar_historico()
                    if historico:
                        for i, (hash_id, dados) in enumerate(historico.items(), 1):
                            print(f"{i}. {dados['nome']} - {dados['data_postagem'][:19]}")
                        print(f"\nTotal: {len(historico)} atletas postados hoje")
                    else:
                        print("Nenhum atleta no histórico hoje")
                except Exception as e:
                    print(f"Erro ao carregar histórico: {e}")
                print("="*50)
                input("\nPressione Enter para continuar...")
                
            elif opcao == '5':
                resposta = input("\nTem certeza que deseja limpar o histórico? (s/N): ")
                if resposta.lower() in ['s', 'sim', 'y', 'yes']:
                    try:
                        if Path(monitor.arquivo_historico).exists():
                            os.remove(monitor.arquivo_historico)
                        print("Histórico limpo!")
                    except Exception as e:
                        print(f"Erro ao limpar histórico: {e}")
                else:
                    print("Operação cancelada")
                input("\nPressione Enter para continuar...")
                
            elif opcao == '6':
                print("\nEncerrando programa...")
                if monitor.running:
                    monitor.parar_monitoramento()
                break
                
            else:
                print("Opção inválida! Digite 1-6.")
                
        except KeyboardInterrupt:
            print("\n\nPrograma interrompido pelo usuário")
            if monitor.running:
                monitor.parar_monitoramento()
            break
        except Exception as e:
            print(f"Erro: {e}")

def main():
    """Função principal"""
    print("="*60)
    print("MONITOR AUTOMÁTICO BID CBF v2.2")
    print("   Desenvolvido para monitoramento de contratos")
    print("   Com controle de duplicatas e limpeza automática diária")
    print("="*60)
    
    # Configurar handler para sinais
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Verificações iniciais
    if not verificar_dependencias():
        sys.exit(1)
    
    if not verificar_arquivos():
        sys.exit(1)
    
    # Verificar argumentos da linha de comando
    if len(sys.argv) > 1:
        monitor = BIDMonitor()
        
        if sys.argv[1] == '--auto':
            # Modo automático direto
            logger.info("Iniciando em modo automático...")
            monitor.iniciar_monitoramento()
        elif sys.argv[1] == '--once':
            # Execução única
            logger.info("Executando verificação única...")
            monitor.executar_unica_vez()
        elif sys.argv[1] == '--status':
            # Mostrar status
            monitor.exibir_status()
        elif sys.argv[1] == '--help':
            print("\nUso:")
            print("  python main.py          - Modo interativo")
            print("  python main.py --auto   - Monitoramento automático")
            print("  python main.py --once   - Execução única")
            print("  python main.py --status - Exibir status")
            print("  python main.py --help   - Exibir esta ajuda")
        else:
            print(f"Argumento inválido: {sys.argv[1]}")
            print("   Use --help para ver as opções disponíveis")
    else:
        # Modo interativo
        menu_interativo()

if __name__ == "__main__":
    main()