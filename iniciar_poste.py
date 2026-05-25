# iniciar_poste.py

from dispositivos import Atuador

if __name__ == "__main__":
    # Cria uma instância de um Atuador do tipo 'LAMP_POST'
    # O nome precisa ser igual ao do enum no .proto, sem o prefixo.
    poste = Atuador(tipo='LAMP_POST')
    
    # Inicia o dispositivo. Ele cuidará de toda a lógica de rede a partir daqui.
    poste.iniciar()