# Clair UI (prova de conceito)

Interface Gradio (v2) para testar a API do Clair. Três abas: **Configuração** (sub-abas Demo, Simulação e Blacklist), **Chat** e **Analytics** (sub-seções Gráficos, Estado ao vivo e Servidor) (grupo de 6 pessoas + Clair).

## Como subir

Requisitos: Docker com Compose. O `docker-compose.yml` sobe três serviços: `clair` (imagem `ghcr.io/adaj/clair:v0.4.6`), `mongo-db` e `clair-ui` (construído a partir desta pasta).

1. `cp .env.example .env` e preencha `CLAIR_TOKEN` (veja "Gerar o token") e, para a simulação com o Claude Haiku, `COLLABSIM_ANTHROPIC_API_KEY`. O `.env` está no `.gitignore`: nunca o versione.
2. `docker compose up -d --build` e abra http://localhost:7860 (a porta é publicada só em 127.0.0.1; a UI não tem login).
3. Depois de mudar o `.env`, recrie o container: `docker compose up -d clair-ui`.

Variáveis de ambiente (única configuração): `CLAIR_URL` (padrão `http://clair:8000`) e `CLAIR_TOKEN`. O token nunca é exibido na UI nem em mensagens de erro. Sem `CLAIR_TOKEN` a UI sobe com um aviso.

## Gerar o token

```
docker compose exec clair python app/db/generate_token.py <parceiro> <meses>
```

Coloque o valor em `CLAIR_TOKEN` no `.env` e reinicie o `clair-ui`.

## Primeiro uso

Configuração > Demo: informe o learning space (ex. `teste01`), deixe "Clair ativa" ligada, escolha modo e idioma (o guia "Qual modo escolher?" resume os modos) e clique em **Salvar e ativar**. Depois use a aba Chat. Sem configuração, o envio retorna "não há agente ativo".

- Só learning space, ativa, modo, idioma e palavras-chave vão ao Clair. Avatar da Clair, grupo, nomes dos participantes, consulta proativa (pull) e detalhes técnicos são da interface e valem na hora, sem salvar.
- A sub-aba Demo não edita a blacklist. O POST /configuration SEMPRE envia `blacklist`: a UI lê a configuração atual (`GET /configuration/{learning_space}`) e reenvia a lista existente; se o GET der 404 ou não houver configuração anterior, envia `blacklist: []`; se o GET falhar por outro motivo (rede, token, 5xx, resposta ilegível), nada é salvo e a UI explica (para nunca apagar uma lista por engano). O indicador "Pronta" não depende da blacklist.
- Avatares: `assets/pessoa_1.png` a `pessoa_6.png` (256 px; reduzidos para 168 px só no CSS embutido). A Clair usa o avatar 2 por padrão e as Pessoa1..6 usam 1, 3, 4, 5, 6 e 2 (a UI só avisa quando duas Pessoas repetem o avatar; Clair e Pessoa podem repetir sem aviso); cada um é escolhido na própria sub-aba Demo. O avatar é só visual (não vai ao Clair), pode se repetir (a UI apenas avisa) e a mensagem guarda o avatar que a pessoa tinha ao ser enviada. Se um PNG faltar, aparece uma silhueta neutra e a UI continua funcionando.
- [a confirmar] O Clair guardaria o estado do diálogo por (learning space, grupo) e a saudação de "duas pessoas entraram" só ocorreria uma vez por grupo (não verificado no servidor): use **Novo grupo** (ao lado do campo Grupo e no Chat) para gerar um id novo (`g-4f7a`) e começar limpo; trocar o grupo à mão também limpa o chat. Não altera a configuração nem o indicador "Pronta".
- Na sub-aba Demo, "Opções da demo" fica à esquerda e "Configuração da Clair" (com Salvar e ativar) à direita; no celular a Configuração vem primeiro. O botão **Testar** só abre a aba Chat (não salva nada nem bloqueia); o Chat mostra "Clair pronta" ou "alterações não salvas".
- Analytics > Gráficos (matplotlib, `plots_mpl.py`): 10 gráficos da sessão atual (registros do chat; `intervention` guarda se a Clair interveio), desenhados ao abrir a aba ou em "Atualizar gráficos" (não a cada mensagem), em PNG claro + escuro (alternados por variáveis CSS conforme `body.dark`). O texto dentro dos gráficos é sempre em inglês (fonte DejaVu, sem fonte CJK); só título/descrição/avisos do cartão seguem o idioma do site. "Analytics do servidor" usa `analytics_adapter.py`, que aceita qualquer JSON de `GET /analytics` (gráfico, tabela ou JSON; nunca levanta exceção). `plots_synth.py` (dados sintéticos) é só para testes/mock e não vai na imagem. O Dockerfile define `MPLBACKEND=Agg` e `MPLCONFIGDIR` gravável pelo usuário 10001; matplotlib tem wheel manylinux (sem apt).
- Idioma do site (PT, EN, ES, NL, DE, 中文): seletor no cabeçalho; o inicial vem do idioma do navegador (senão PT) e a escolha fica no `localStorage` do visitante (só comodidade). A troca é feita sem recarregar e não apaga campos digitados nem o histórico do chat. Textos em `locales/<lang>.json` (chaves estáveis, placeholders `{x}`; PT é a fonte; fallback idioma -> en -> pt); o guia de modos lê `modes_help.<lang>.json` (cai em `modes_help.json`). Modos e códigos de idioma da Clair não são traduzidos.
- O rótulo "apt-base (padrão)" é só de exibição; o valor enviado à API é `apt-base`.
- O guia de modos é lido em tempo de execução de `modes_help.json` (rascunho a confirmar pelo autor; marcadores `[a confirmar]` são preservados). Se o arquivo faltar ou for inválido, a UI mostra um aviso discreto e continua funcionando.

## Simulação (alunos simulados com collabsim)

A sub-aba **Configuração > Simulação** cria um grupo de alunos simulados pelo pacote [collabsim](https://github.com/adaj/collabsim) (v0.5.3, instalado pelo `requirements.txt` a partir do tarball da tag, sem precisar de git na imagem). Os alunos conversam no **learning space** e no **grupo** da sub-aba Demo (grupo vazio = um id novo `g-xxxx`):

- cada mensagem de um aluno simulado vai ao Clair pela API real (`POST /message`, `username` = nome do aluno), exatamente como a de um aluno real;
- as intervenções da Clair (da resposta do `/message` e do `/pull`, no intervalo da consulta proativa da sub-aba Demo) aparecem no chat e são repassadas aos alunos, que reagem a elas; a mesma intervenção não se repete (mesma janela de 2 min do chat manual);
- durante a simulação você **só observa**: a caixa de mensagem e a escolha de quem fala somem do Chat e, no lugar, ficam o status e os botões Pausar / Retomar / Parar. Ao terminar (ou ao Parar), o chat manual volta. Limpar, Novo grupo ou trocar o grupo encerram a simulação.

Controles: número de alunos (2 a 6, um avatar para cada; o collabsim foi calibrado para 2 a 4), tarefa (biblioteca do collabsim), idioma dos alunos (automático = pt-BR com a interface em PT, inglês nos demais), conflito (baixo / normal / alto), ritmo (piloto ≈ 33 min simulados; rápido ≈ 15 min), velocidade (1× a 4×, padrão 3×: uma sessão piloto leva ~11 min reais), semente opcional e backend. A linha de status mostra estado, minutos simulados, mensagens e custo (`usage()` do collabsim).

### Chave da Anthropic

O backend **LLM** usa o Claude Haiku e precisa de uma chave. Acrescente ao `.env` da raiz (ao lado do `docker-compose.yml`; o `.env` já é ignorado pelo git):

```
COLLABSIM_ANTHROPIC_API_KEY=sk-ant-...
```

O compose repassa só essa variável ao `clair-ui` (`COLLABSIM_ANTHROPIC_API_KEY=${COLLABSIM_ANTHROPIC_API_KEY:-}`); a UI também aceita `ANTHROPIC_API_KEY` como alternativa. A chave nunca aparece na UI nem em logs. Depois de editar o `.env`, recrie o container: `docker compose up -d --build clair-ui`. Sem chave, a sub-aba avisa e só o backend **mock** funciona (offline, determinístico, custo zero; bom para demonstrar o fluxo, com falas menos naturais). **Verificar conexão** faz uma chamada de 1 token ao Haiku (custo desprezível) e mostra o resultado; não há limite de gasto no pacote: defina um limite no Console da Anthropic. Custo típico com LLM: ≈ US$ 0,01–0,02 por sessão piloto.

Limites e cuidados: no máximo `COLLABSIM_MAX_SESSIONS` simulações simultâneas no processo (padrão 4); uma simulação que ninguém consulta por 10 min é encerrada pelo collabsim (aba fechada); a velocidade acelera o relógio simulado, mas os `timestamp` enviados ao Clair são os do relógio real (a Clair vê intervalos 3× menores que os de uma conversa real); cada mensagem simulada é um `POST /message` síncrono, então um Clair lento atrasa o chat (os erros aparecem na faixa de aviso e a simulação continua).

## Rodar em um Hugging Face Space

A UI não precisa de caminhos absolutos e escuta em `0.0.0.0`, na porta de `GRADIO_SERVER_PORT` ou `PORT` (padrão 7860; host em `GRADIO_SERVER_NAME`).

1. Crie um Space com SDK Docker (use o `Dockerfile` desta pasta) e, no front matter do README do Space, `app_port: 7860`.
2. O Clair precisa ser acessível por URL pública (por exemplo, por um túnel do tipo Cloudflare Tunnel ou ngrok). Em Settings do Space defina `CLAIR_URL` (variável ou Secret, com `https://...`) e `CLAIR_TOKEN` como **Secret** (nunca como variável pública nem no código).
3. Atenção: a UI não tem login. Quem abrir o Space usa o token do Clair; deixe o Space privado ou proteja o túnel.
4. Se o servidor não responder, a UI mostra "Clair offline" na linha de status e uma faixa de aviso clara, sem traceback e sem exibir a URL nem o token.

## Testes (sem Docker)

```
pip install -r requirements-dev.txt
pytest                # test_client.py (cliente) e test_app.py (UI, guia, blacklist, offline)
pytest tests          # tests/test_sim_bridge.py: simulação com collabsim backend mock e um Clair falso (sem rede, sem custo)
python smoke_app.py   # handlers da UI contra o mock, sem navegador
python ui_check.py    # opcional: capturas e verificações em Chromium headless (Playwright já instalado)
```

Os testes usam `mock_clair.py` (imita o contrato e os fatos observados; não é o Clair real). `ui_check.py` grava capturas a 1280 e 380 px, claro e escuro, e confere erros de console e rolagem horizontal.

## Limitações

- Estado do chat, histórico e tabela ao vivo ficam na sessão do navegador (memória do servidor); recarregar a página limpa tudo.
- Os "usuários" Pessoa1..Pessoa6 são só nomes enviados em `username`; não há login.
- Pull proativo é por polling (padrão 30 s). O balão tracejado "Interveio por conta própria" só aparece para intervenções vindas do `/pull`. A mesma intervenção só é descartada se já apareceu há menos de 2 minutos (ex.: veio na resposta do /message e volta no /pull); depois disso, ou em outro grupo, uma saudação repetida vale de novo. O pull é pulado enquanto a configuração verificada indicar "não configurada", "sem acesso" ou "inativa".
- A UI foi validada em Chromium headless contra o mock; não foi testada em Safari/iOS, leitor de tela real nem contra o Clair real.
- A marca "Clair ativa" do chat reflete o último Salvar/Carregar desta sessão, não é consultada ao servidor (recarregar a página volta a "Configuração não salva").
- O CSS depende de classes internas do Gradio 6.30 em alguns pontos (versão fixada em requirements.txt); se mudarem, a tela continua funcional com os componentes nativos, só menos polida.

## Incertezas

Formatos não observados no servidor real, tratados de forma defensiva:

- `agent_intervention` e `selected_move` quando a Clair intervém: se dict com `message` (ou `text`), mostra o texto; se string, mostra a string; outro formato aparece como JSON bruto recolhível. `selected_move` é mostrado como veio (JSON se não for texto).
- Resposta de `GET /pull`: aceita `null`, objeto (com ou sem `agent_intervention` aninhado) ou lista. Interventions repetidas são deduplicadas por hash do conteúdo. O parâmetro `timestamp` é enviado como o instante da consulta anterior menos 5 s (a semântica real não está documentada; a deduplicação cobre repetições, mas se o servidor interpretar o valor de outra forma intervenções podem não chegar). A primeira consulta vai sem `timestamp`.
- Respostas de `GET /languages/{mode}`, `GET /configuration/{ls}`, `GET /blacklist` e `GET /blacklist-user`: aceita lista de strings ou objetos; blacklist é exibida como JSON. Se `/languages` falhar, o idioma pode ser digitado.
- `GET /analytics`: sempre JSON; vira tabela só se for lista de objetos.
- Contrato extraído de um resumo do openapi.json, não do JSON literal; limites de tamanho (50/5/200 caracteres) são validados no cliente a partir dele.
- `retrieve_details` e `save` são enviados com o padrão (true); o efeito de `false` não é conhecido.

## Segurança, concorrência e limites

- **UI sem login: mantenha o Space PRIVADO ou protegido** (túnel autenticado, proxy com senha). Qualquer visitante pode reconfigurar o learning space, gravar blacklist e usar o token do Clair. Tornar o Space público é uma decisão do autor e exige acrescentar autenticação antes.
- O **learning space é compartilhado** por todos que usam o mesmo servidor Clair: dois visitantes com o mesmo learning space reconfiguram o mesmo agente. O **grupo** padrão é gerado por sessão (`g-xxxx`), então visitantes diferentes não misturam conversas por acidente; o learning space continua editável.
- Concorrência: até 8 eventos simultâneos (fila de 64); dentro de uma sessão os envios e o pull são serializados e "Limpar", "Novo grupo" ou troca de grupo descartam respostas que chegarem depois (contador `epoch` por sessão). Os gráficos são desenhados um por vez (matplotlib não é thread-safe).
- Mensagens: no máximo 2000 caracteres (UI e cliente); mude com `CLAIR_MAX_MESSAGE_CHARS`. O cliente não segue redirecionamentos (o token `access_token` nunca é reenviado a outro host), ignora proxies do ambiente e recusa `.` e `..` como learning space, grupo ou usuário.
- Token 403: a UI mostra só "token ausente ou inválido". Para gerar um token use o comando do passo anterior (`generate_token.py`), fora da UI.

## Licença

MIT (veja `LICENSE`).
