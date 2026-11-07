# DublaSync

Junta a **dublagem** de um arquivo (DVD, TV, VHS, só o áudio…) com o **vídeo em alta qualidade** de outro e gera um **MKV dual áudio**, com a dublagem sincronizada sozinha — inclusive quando a dublagem está em velocidade PAL ou tem cenas a mais/a menos.

Existem duas versões, neste mesmo repositório:

| | Onde roda | Como usa |
|---|---|---|
| **Servidor** (recomendado se os filmes ficam no NAS) | No próprio servidor ZimaOS, em Docker | Pelo navegador, em `http://IP-DO-SERVIDOR:8282` — no PC ou no celular |
| **Windows** | No seu PC | Programa com janela (`Abrir DublaSync.bat`) |

Na versão servidor o filme **nunca passa pela rede**: o servidor lê e grava direto no disco dele. É bem mais rápido que processar no PC um arquivo que está no NAS.

---

## Instalar no servidor (ZimaOS) — passo a passo

Leva uns 10 minutos. Você só vai usar o navegador do PC; não precisa digitar comando nenhum.

### Antes de começar

- O servidor precisa estar ligado e **com internet** (na primeira vez ele baixa o FFmpeg e o programa).
- Saiba o endereço do painel do ZimaOS. É o mesmo IP que você usa no qui, sem a porta — por exemplo `http://192.168.0.14`.

### Passo 1 — Copiar o texto de instalação

1. Abra o arquivo **[docker-compose.yml](docker-compose.yml)** (aqui mesmo no GitHub).
2. No canto superior direito da caixa com o texto, clique no ícone **Copy raw file** (dois quadradinhos sobrepostos). Isso copia tudo.

> Não precisa entender nem mudar nada nesse texto.

### Passo 2 — Importar no ZimaOS

1. No navegador do PC, abra o painel do ZimaOS (ex.: `http://192.168.0.14`) e entre com seu usuário.
2. Clique no **+** no canto superior direito do painel.
3. Escolha **Install a customized app** (Instalar um app personalizado).
4. Na janela que abrir, clique em **Import** (canto superior direito).
5. Clique na aba **Docker Compose**, cole o texto (Ctrl+V) e clique em **Submit**.
6. Se aparecer um aviso, clique em **OK**.

### Passo 3 — Conferir e instalar

Vai aparecer um formulário já preenchido. Confira só três coisas, **sem mudar nada**:

- **Docker Image**: `python:3.12-slim`
- **Port / Web UI**: `8282`
- **Volumes**: duas linhas, `/DATA` → `/DATA` e `/media` → `/media` (é assim que o DublaSync enxerga os seus discos)

Clique em **Install**.

### Passo 4 — Esperar a primeira vez

Na primeira vez ele demora **de 3 a 5 minutos** para ficar pronto: instala o FFmpeg e baixa o DublaSync do GitHub. O ícone aparece no painel antes de terminar — se você clicar e a página não abrir, espere mais um pouco e tente de novo.

### Passo 5 — Abrir

Clique no ícone **DublaSync** no painel do ZimaOS, ou digite no navegador:

```
http://192.168.0.14:8282
```

(troque pelo IP do seu servidor). Funciona também no celular, conectado no mesmo Wi-Fi. Vale salvar nos favoritos.

### Passo 6 — Primeiro uso

1. Em **Novo trabalho**, clique na área **Arquivo dublado**. Abre um navegador das pastas do servidor:
   - **/media** = discos e HDs (o seu de 8 TB deve estar aqui dentro, ex.: `/media/HD 8TB`)
   - **/DATA** = armazenamento interno do ZimaOS
2. Entre nas pastas e clique no arquivo dublado. Faça o mesmo em **Original em alta qualidade**.
3. Confira o nome sugerido e clique **Gerar agora** (ou **Adicionar à fila** para juntar vários).
4. Pode fechar a página: o servidor continua trabalhando. Abra de novo quando quiser para ver o andamento.

Para vários filmes de uma vez: página **Fila → Adicionar vários**, marque os dublados e os originais (misturados, pode ser de pastas diferentes) e clique **Formar pares**.

### Passo 7 (opcional) — Mandar direto para o qBittorrent

Assim cada filme pronto já entra no qBittorrent e fica semeando, sem você fazer nada.

1. No DublaSync, vá em **Configurações → qBittorrent**.
2. **Endereço**: o mesmo do qBittorrent no qui (ex.: `http://192.168.0.14:8181`).
3. **Usuário** e **Senha**: os do qBittorrent (os mesmos que você colocou no qui).
4. **Pasta aqui** e **Mesma pasta no qBittorrent** — o mesmo disco tem nomes diferentes para cada app. Para descobrir:
   - No painel do ZimaOS, passe o mouse no ícone do **qBittorrent**, clique nos **três pontinhos (⋮)** → **Settings**.
   - Na parte **Volumes**, procure a linha do seu HD. A coluna da esquerda (**Host**) é o caminho no servidor, ex.: `/media/HD 8TB`; a da direita (**Container**) é como o qBittorrent chama, ex.: `/HD 8TB`.
   - Coloque a da esquerda em **Pasta aqui** e a da direita em **Mesma pasta no qBittorrent**.
5. Clique **Testar conexão** (tem que aparecer ✔ conectado) e ligue **Enviar ao qBittorrent**.

Na primeira vez, deixe **Pular verificação** desligado: o qBittorrent confere o arquivo e passa para "Semeando". Se ele começar a **baixar do zero**, a tradução de pastas está errada — pare o torrent e confira o passo 4.

---

## Atualizar

Quando sair uma versão nova aqui no GitHub:

1. No painel do ZimaOS, passe o mouse no ícone do **DublaSync** → **três pontinhos (⋮)** → **Restart** (Reiniciar).
2. Ao ligar, ele busca a versão nova sozinho (leva poucos segundos).

Para conferir, veja embaixo do menu lateral do DublaSync: **atualizado: data e hora** da versão instalada.

Suas configurações, fila e histórico ficam em `/DATA/AppData/dublasync/dados` e **não se perdem** ao atualizar.

---

## Problemas comuns

**A página não abre**
- Na primeira instalação, espere 5 minutos.
- No painel do ZimaOS, veja se o DublaSync está ligado (ícone colorido). Se não, clique nele para ligar.
- A porta 8282 pode estar ocupada por outro app: ⋮ → **Settings** → troque o primeiro número da porta (ex.: `8283`) → **Save**, e abra `http://IP:8283`.

**Não acho meu HD na lista**
- Procure dentro de `/media` e de `/DATA`. Discos externos e extras ficam em `/media`.

**Ficou "Erro" na fila**
- Dê dois cliques no filme para ver a mensagem. Botão **Tentar de novo** coloca na fila outra vez.
- Em **Histórico → Registro técnico** tem o detalhe completo (útil para me mandar).

**"Retirar os arquivos antigos" vem desligado**
- De propósito: no servidor, o original normalmente está **semeando no qBittorrent**. Se o DublaSync mover o original, aquele torrent dá "arquivo ausente". Ligue só se os originais não estiverem semeando. Quando ligado, os arquivos vão para uma pasta `_antigos (DublaSync)` ao lado deles (nada é apagado de vez).

**Onde ficam os arquivos temporários**
- Em `/DATA/AppData/dublasync/temp`. Um filme 5.1 de 2 h usa até ~15 GB durante o processo, e tudo é apagado no fim.

## Desinstalar

Painel do ZimaOS → ⋮ no ícone do DublaSync → **Uninstall**. Os filmes gerados continuam onde estão. Para apagar também as configurações, apague a pasta `/DATA/AppData/dublasync` pelo app **Files**.

---

## Versão Windows

1. Aqui no GitHub: botão verde **Code → Download ZIP** e extraia em qualquer pasta.
2. Rode `instalar.bat` uma vez (instala Python, FFmpeg e bibliotecas).
3. Abra pelo `Abrir DublaSync.bat`. Detalhes no `LEIA-ME.txt`.

Para atualizar: baixe o ZIP de novo e extraia por cima.
