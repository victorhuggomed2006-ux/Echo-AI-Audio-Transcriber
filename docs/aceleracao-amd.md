# Aceleração da transcrição em placas AMD e Intel: relatório técnico

Estado: **Fase 1 entregue** (reconhecimento, `echo_ai/gpus.py`); **Fase 2 não implementada de propósito** (nenhuma opção
passou nos critérios com evidência). A transcrição em AMD e Intel segue na CPU, e a tela diz isso com honestidade.

## Critérios (do pedido)

Funciona em AMD dedicada **e** integrada; Windows e Linux; não incha o instalador (baixa pela tela, como o pacote NVIDIA);
só releases oficiais, com sha256 conferido; licença compatível com MIT; sem administrador; CPU como plano B.

## Opção A: whisper.cpp com Vulkan (binário oficial como subprocesso)

Era a favorita (Vulkan roda em AMD, Intel e NVIDIA, integrada ou dedicada, e o whisper.cpp é MIT).

**Evidência (verificada em 14/11/2026, release b5454 / v1.9.5 do ggml-org/whisper.cpp):** os releases oficiais **não trazem
binário com Vulkan**. Os arquivos de Windows são: `whisper-bin-x64.zip` e `Win32` (CPU), `whisper-blas-bin-*` (CPU com BLAS),
`whisper-bin-win-cuda-*` e `whisper-cublas-*` (NVIDIA, 270 a 685 MB), `whisper-bin-win-opencl-adreno-arm64.zip` (só
Qualcomm Adreno) e `win-cpu-arm64`. No Linux há só `whisper-bin-ubuntu-x64` e `-arm64`. Baixei os dois de x64 (8,9 MB e 10,4
MB, sha256 igual ao publicado pelo GitHub) e listei o conteúdo: só `ggml-cpu-*.dll/.so` (variantes por processador), nenhum
`ggml-vulkan`. O Mac tem o xcframework (Metal), que não serve a um subprocesso.

Consequência: usar Vulkan exigiria **compilar o whisper.cpp nós mesmos** (SDK Vulkan, CMake, compilador, nos 3 sistemas) ou
usar build de terceiros. Isso quebra o critério "só releases oficiais com sha256 do fornecedor" e cria um pipeline de
build/assinatura para manter. Não testei o ganho de velocidade (o teste real exigiria o binário que não existe oficialmente;
não baixei modelo nem rodei nada).

Risco se for adiante: o binário seria nosso (responsabilidade de segurança e de atualização); drivers Vulkan antigos ou buggy
nas integradas; o ggml-vulkan usa memória compartilhada nas integradas (competir com os 16 GB do usuário).

## Opção B: ONNX Runtime com DirectML (só Windows)

Roda em AMD e Intel integradas e dedicadas no Windows 10/11 sem driver especial. Contras: **só Windows** (falha o critério de
Linux); exige modelos Whisper convertidos para ONNX (outro formato, outro repositório, outro tokenizador, sem o
faster-whisper e sem o VAD/beam search que o app usa hoje); o pacote `onnxruntime-directml` é grande (dezenas de MB) e o
DirectML está em modo de manutenção (a Microsoft direciona para o WinML). Reescrever o `Motor` com decodificação
autorregressiva em ONNX é trabalho grande e frágil para um ganho incerto. Não recomendo.

## Opção C: CTranslate2 com ROCm (só Linux)

O CTranslate2 oficial (PyPI) só acelera CUDA; ROCm existe em forks não oficiais. O ROCm no Linux suporta oficialmente poucas
placas (RDNA2 como a RX 6600 está fora da lista oficial; precisa de gambiarra com variável de ambiente) e **não suporta as
integradas** dos Ryzen. Pede instalação do ROCm (vários GB, root). Falha vários critérios. Descartada.

## Recomendação e próximos passos

1. Manter o que foi entregue: reconhecer e informar (Fase 1). Ninguém é enganado sobre o que roda onde.
2. Se o dono quiser mesmo aceleração em AMD/Intel: o caminho mais promissor é a **opção A**, mas só depois de uma destas
   condições: (a) o projeto whisper.cpp publicar binário Vulkan nos releases oficiais (acompanhar issues/releases; o `Motor`
   seria um segundo motor por subprocesso `whisper-cli`/`whisper-server`, com modelos ggml do Hugging Face baixados pela
   tela e sha256 conferido); ou (b) o dono aceitar manter um build próprio no GitHub Actions (Windows e Linux, Vulkan),
   publicado nos releases do Echo-AI com sha256, e a decisão de segurança de distribuir binário nosso.
3. Antes de qualquer decisão, medir: whisper.cpp+Vulkan (tiny/base/small) contra faster-whisper CPU int8 na RX 6600 (RTF),
   porque a CPU do dono (Ryzen 5 5500, 6 núcleos) já faz o `small` em tempo razoável, e o ganho em integradas fracas
   pode ser pequeno.
4. O desenho já comporta isso: `Motor` é um Protocol, `Placa.carregar` recebe o dispositivo, e `Maquina.gpus` (Fase 1) diz
   que placa existe para a escolha do motor.
