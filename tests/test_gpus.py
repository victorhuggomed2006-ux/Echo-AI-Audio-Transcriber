"""Testes do reconhecimento das placas de vídeo (echo_ai.gpus): saídas simuladas de Windows, Linux e Mac, sem tocar o
sistema de verdade, então valem iguais em qualquer PC."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from echo_ai import gpus, hardware
from echo_ai.gpus import DetectorDeGpus, ExecutorReal, Fabricante, Gpu, SistemaReal, e_integrada, principal

GB = 1024**3


class ExecutorFalso:
    """Responde pelo nome do programa (primeira palavra do comando); programa fora da tabela = não existe (None)."""

    def __init__(self, respostas: dict[str, str | None]) -> None:
        self.respostas = respostas
        self.chamadas: list[tuple[str, ...]] = []

    def executar(self, comando: Sequence[str], tempo_s: float) -> str | None:
        self.chamadas.append(tuple(comando))
        assert tempo_s > 0
        return self.respostas.get(comando[0])


class SistemaFalso:
    """Um sistema de arquivos de mentira: `arquivos` mapeia caminho -> texto; as pastas saem dos próprios caminhos."""

    def __init__(self, nome: str, arquivos: dict[str, str] | None = None) -> None:
        self.nome = nome
        self.arquivos = arquivos or {}

    def ler_texto(self, caminho: str) -> str | None:
        return self.arquivos.get(caminho)

    def listar(self, caminho: str) -> list[str]:
        prefixo = caminho.rstrip("/") + "/"
        return sorted({c[len(prefixo) :].split("/")[0] for c in self.arquivos if c.startswith(prefixo)})


def detectar(nome_do_sistema: str, respostas: dict[str, str | None], arquivos: dict[str, str] | None = None) -> tuple[Gpu, ...]:
    return DetectorDeGpus(SistemaFalso(nome_do_sistema, arquivos), ExecutorFalso(respostas)).detectar()


# ---------------------------------------------------------------- Windows


def saida_do_windows(placas: list[dict[str, object]], memorias: list[dict[str, object]]) -> str:
    return json.dumps({"placas": placas, "memorias": memorias})


RX_6600 = {
    "nome": "AMD Radeon RX 6600",
    "fabricante": "Advanced Micro Devices, Inc.",
    "ram": 4293918720,  # o teto de 32 bits do Windows: a memória de verdade (8 GB) vem do registro
    "id": "PCI\\VEN_1002&DEV_73FF&SUBSYS_2413148C&REV_C7\\6&2735A9FB&0&00000009",
}


def test_windows_amd_dedicada_usa_a_memoria_do_registro_e_nao_o_teto_de_4_gb() -> None:
    memorias = [{"nome": "AMD Radeon RX 6600", "bytes": 8573157376}] * 3  # o registro repete a placa várias vezes
    achadas = detectar("windows", {"powershell": saida_do_windows([RX_6600], memorias)})
    assert achadas == (Gpu(Fabricante.AMD, "AMD Radeon RX 6600", 8573157376 / GB, False),)


def test_windows_amd_integrada_do_ryzen_e_nvidia_dedicada_juntas() -> None:
    placas = [
        {
            "nome": "AMD Radeon(TM) Graphics",
            "fabricante": "Advanced Micro Devices, Inc.",
            "ram": 536870912,
            "id": "PCI\\VEN_1002&DEV_1638",
        },
        {"nome": "NVIDIA GeForce RTX 4060 Laptop GPU", "fabricante": "NVIDIA", "ram": 4293918720, "id": "PCI\\VEN_10DE&DEV_28E0"},
    ]
    achadas = detectar("windows", {"powershell": saida_do_windows(placas, [])})
    assert [(g.fabricante, g.nome, g.integrada) for g in achadas] == [
        (Fabricante.AMD, "AMD Radeon Graphics", True),
        (Fabricante.NVIDIA, "NVIDIA GeForce RTX 4060 Laptop GPU", False),
    ]
    assert achadas[0].memoria_gb == 0.5  # sem o registro, o AdapterRAM abaixo do teto vale
    assert achadas[1].memoria_gb is None  # no teto e sem o registro: melhor não chutar (o nvidia-smi completa depois)


def test_windows_um_so_objeto_sem_lista_e_fabricante_pelo_codigo_pci() -> None:
    saida = json.dumps(
        {"placas": {"nome": "Controlador desconhecido", "fabricante": None, "ram": None, "id": "PCI\\VEN_8086&DEV_46A6"}}
    )
    achadas = detectar("windows", {"powershell": saida})
    assert [(g.fabricante, g.integrada) for g in achadas] == [(Fabricante.INTEL, True)]


def test_windows_ignora_adaptadores_virtuais() -> None:
    placas = [
        {"nome": "Microsoft Basic Display Adapter", "fabricante": "Microsoft", "ram": 0, "id": ""},
        {"nome": "Microsoft Remote Display Adapter", "fabricante": "Microsoft", "ram": 0, "id": ""},
        RX_6600,
    ]
    assert [g.nome for g in detectar("windows", {"powershell": saida_do_windows(placas, [])})] == ["AMD Radeon RX 6600"]


@pytest.mark.parametrize(
    "saida", [None, "", "   ", "isto não é JSON", "[1, 2]", '{"placas": 5}', '{"placas": [null, 3, {"nome": 7}]}']
)
def test_windows_falha_ou_lixo_vira_lista_vazia(saida: str | None) -> None:
    assert detectar("windows", {"powershell": saida}) == ()


def test_windows_consulta_o_powershell_sem_perfil_e_sem_exigir_administrador() -> None:
    executor = ExecutorFalso({"powershell": saida_do_windows([RX_6600], [])})
    DetectorDeGpus(SistemaFalso("windows"), executor).detectar()
    comando = executor.chamadas[0]
    assert comando[:4] == ("powershell", "-NoProfile", "-NonInteractive", "-Command")
    assert "Win32_VideoController" in comando[4]


# ---------------------------------------------------------------- Linux

LSPCI_RX_6600 = (
    '03:00.0 "VGA compatible controller [0300]" "Advanced Micro Devices, Inc. [AMD/ATI] [1002]" '
    '"Navi 23 [Radeon RX 6600/6600 XT/6600M] [73ff]" -rc7 -p00 "Sapphire Technology Limited [1da2]" "Device [e445]"\n'
    '00:14.0 "USB controller [0c03]" "Advanced Micro Devices, Inc. [AMD] [1022]" "500 Series Chipset USB 3.1 XHCI [43ee]" -p30\n'
)
LSPCI_APU_E_NVIDIA = (
    '00:02.0 "VGA compatible controller [0300]" "Intel Corporation [8086]" "Alder Lake-P GT2 [Iris Xe Graphics] [46a6]" -p00\n'
    '01:00.0 "3D controller [0302]" "NVIDIA Corporation [10de]" "AD107M [GeForce RTX 4050 Max-Q / Mobile] [28a1]" -ra1 -p00\n'
    '06:00.0 "Display controller [0380]" "Advanced Micro Devices, Inc. [AMD/ATI] [1002]" "Phoenix1 [Radeon 780M] [15bf]" -p00\n'
)


def arquivos_drm(placas: dict[str, tuple[str, str, str, str | None]]) -> dict[str, str]:
    """placas: cardN -> (vendor, device, endereço PCI, bytes de memória de vídeo ou None)."""
    arquivos: dict[str, str] = {"/sys/class/drm/card0-DP-1/status": "connected", "/sys/class/drm/renderD128/dev": "226:128"}
    for card, (vendor, device, endereco, memoria) in placas.items():
        base = f"/sys/class/drm/{card}/device"
        arquivos[f"{base}/vendor"] = vendor + "\n"
        arquivos[f"{base}/device"] = device + "\n"
        arquivos[f"{base}/class"] = "0x030000\n"
        arquivos[f"{base}/uevent"] = f"DRIVER=amdgpu\nPCI_SLOT_NAME={endereco}\n"
        if memoria:
            arquivos[f"{base}/mem_info_vram_total"] = memoria + "\n"
    return arquivos


def test_linux_amd_dedicada_junta_o_nome_do_lspci_com_a_memoria_do_sys() -> None:
    drm = arquivos_drm({"card0": ("0x1002", "0x73ff", "0000:03:00.0", "8573157376")})
    achadas = detectar("linux", {"lspci": LSPCI_RX_6600}, drm)
    assert achadas == (Gpu(Fabricante.AMD, "AMD Radeon RX 6600", 8573157376 / GB, False),)


def test_linux_notebook_com_intel_integrada_nvidia_e_amd_apu() -> None:
    achadas = detectar("linux", {"lspci": LSPCI_APU_E_NVIDIA})
    assert [(g.fabricante.value, g.nome, g.integrada) for g in achadas] == [
        ("intel", "Intel Iris Xe Graphics", True),
        ("nvidia", "NVIDIA GeForce RTX 4050 Max-Q", False),
        ("amd", "AMD Radeon 780M", True),
    ]


def test_linux_sem_lspci_usa_so_o_sys_e_o_codigo_do_fabricante() -> None:
    drm = arquivos_drm({"card0": ("0x8086", "0x56a0", "0000:03:00.0", None), "card1": ("0x10de", "0x2684", "0000:01:00.0", None)})
    achadas = detectar("linux", {"lspci": None}, drm)
    assert [(g.fabricante.value, g.nome) for g in achadas] == [("intel", "Intel (PCI 56a0)"), ("nvidia", "NVIDIA (PCI 2684)")]


def test_linux_sem_sys_usa_so_o_lspci_e_ignora_o_que_nao_e_video() -> None:
    achadas = detectar("linux", {"lspci": LSPCI_RX_6600})
    assert [(g.nome, g.memoria_gb) for g in achadas] == [("AMD Radeon RX 6600", None)]


def test_linux_sys_e_lspci_do_mesmo_endereco_nao_viram_duas_placas() -> None:
    drm = arquivos_drm({"card0": ("0x1002", "0x73ff", "0000:03:00.0", "8573157376")})
    assert len(detectar("linux", {"lspci": LSPCI_RX_6600.replace("03:00.0", "0000:03:00.0", 1)}, drm)) == 1
    assert len(detectar("linux", {"lspci": LSPCI_RX_6600}, drm)) == 1  # lspci sem o domínio "0000:"


def test_linux_nada_para_ler_e_lista_vazia() -> None:
    assert detectar("linux", {}) == ()


def test_linux_lspci_com_linha_quebrada_nao_derruba() -> None:
    assert detectar("linux", {"lspci": '03:00.0 "VGA aberta\n\nlixo\n'}) == ()


# ---------------------------------------------------------------- Mac


def saida_do_mac(*placas: dict[str, str]) -> str:
    return json.dumps({"SPDisplaysDataType": list(placas)})


def test_mac_com_chip_apple_e_integrada_de_fabricante_outro() -> None:
    m2 = {"_name": "Apple M2", "spdisplays_vendor": "sppci_vendor_Apple", "sppci_bus": "spdisplays_builtin", "sppci_cores": "10"}
    assert detectar("mac", {"system_profiler": saida_do_mac(m2)}) == (Gpu(Fabricante.OUTRO, "Apple M2", None, True),)


def test_mac_intel_com_intel_integrada_e_amd_dedicada() -> None:
    intel = {
        "sppci_model": "Intel UHD Graphics 630",
        "spdisplays_vendor": "sppci_vendor_Intel",
        "sppci_bus": "spdisplays_builtin",
        "spdisplays_vram": "1536 MB",
    }
    amd = {
        "sppci_model": "AMD Radeon Pro 5500M",
        "spdisplays_vendor": "sppci_vendor_amd",
        "sppci_bus": "spdisplays_pcie_device",
        "spdisplays_vram": "8 GB",
    }
    achadas = detectar("mac", {"system_profiler": saida_do_mac(intel, amd)})
    assert achadas == (
        Gpu(Fabricante.INTEL, "Intel UHD Graphics 630", 1.5, True),
        Gpu(Fabricante.AMD, "AMD Radeon Pro 5500M", 8.0, False),
    )


@pytest.mark.parametrize(
    "saida", [None, "", "não é JSON", "[]", '{"SPDisplaysDataType": "x"}', '{"SPDisplaysDataType": [{"x": 1}]}']
)
def test_mac_falha_ou_lixo_vira_lista_vazia(saida: str | None) -> None:
    assert detectar("mac", {"system_profiler": saida}) == ()


# ---------------------------------------------------------------- heurística e escolha


@pytest.mark.parametrize(
    ("fabricante", "nome", "integrada"),
    [
        (Fabricante.AMD, "AMD Radeon RX 6600", False),
        (Fabricante.AMD, "AMD Radeon RX 6800M", False),
        (Fabricante.AMD, "AMD Radeon RX 7600 XT", False),
        (Fabricante.AMD, "AMD Radeon RX Vega 56", False),
        (Fabricante.AMD, "AMD Radeon Pro W6800", False),
        (Fabricante.AMD, "AMD Radeon VII", False),
        (Fabricante.AMD, "AMD Radeon Graphics", True),
        (Fabricante.AMD, "AMD Radeon Vega 8 Graphics", True),
        (Fabricante.AMD, "AMD Radeon RX Vega 11 Graphics", True),  # a integrada do Ryzen 5 2400G leva "RX" no nome
        (Fabricante.AMD, "AMD Radeon 780M", True),
        (Fabricante.AMD, "AMD Radeon 8060S Graphics", True),
        (Fabricante.AMD, "Raphael", True),
        (Fabricante.AMD, "AMD Radeon HD 7870", False),  # sem pista: dedicada
        (Fabricante.INTEL, "Intel UHD Graphics 770", True),
        (Fabricante.INTEL, "Intel Iris Xe Graphics", True),
        (Fabricante.INTEL, "Intel Arc Graphics", True),  # a integrada dos Core Ultra
        (Fabricante.INTEL, "Intel Arc A770 Graphics", False),
        (Fabricante.INTEL, "Intel Arc B580 Graphics", False),
        (Fabricante.INTEL, "Intel Arc Pro A60", False),
        (Fabricante.NVIDIA, "NVIDIA GeForce MX450", False),
        (Fabricante.NVIDIA, "NVIDIA GeForce RTX 4090", False),
        (Fabricante.NVIDIA, "NVIDIA Tegra Orin", True),
        (Fabricante.OUTRO, "Qualquer Coisa", False),
    ],
)
def test_heuristica_de_integrada_ou_dedicada(fabricante: Fabricante, nome: str, integrada: bool) -> None:
    assert e_integrada(fabricante, nome) is integrada


def test_principal_prefere_dedicada_depois_memoria_e_lista_vazia_nao_tem() -> None:
    integrada = Gpu(Fabricante.AMD, "AMD Radeon Graphics", 0.5, True)
    pequena = Gpu(Fabricante.AMD, "AMD Radeon RX 6400", 4.0, False)
    grande = Gpu(Fabricante.AMD, "AMD Radeon RX 6600", 8.0, False)
    assert principal([integrada, pequena, grande]) == grande
    assert principal([integrada]) == integrada
    assert principal([]) is None


def test_fabricante_do_texto() -> None:
    assert gpus.fabricante_do_texto("Advanced Micro Devices, Inc. [AMD/ATI]") is Fabricante.AMD
    assert gpus.fabricante_do_texto("ATI Technologies Inc") is Fabricante.AMD
    assert gpus.fabricante_do_texto("Intel Corporation") is Fabricante.INTEL
    assert gpus.fabricante_do_texto("NVIDIA Corporation") is Fabricante.NVIDIA
    assert gpus.fabricante_do_texto("Matrox") is Fabricante.OUTRO


# ---------------------------------------------------------------- fronteira real e integração com a Maquina


def test_detector_nunca_levanta_erro_mesmo_se_o_sistema_falhar() -> None:
    class Quebrado:
        def executar(self, comando: Sequence[str], tempo_s: float) -> str | None:
            raise RuntimeError("falha inesperada")

    assert DetectorDeGpus(SistemaFalso("windows"), Quebrado()).detectar() == ()


def test_executor_real_trata_programa_inexistente_codigo_de_erro_e_tempo() -> None:
    executor = ExecutorReal()
    assert executor.executar(["programa-que-nao-existe-echo-ai"], 5) is None
    assert executor.executar([sys.executable, "-c", "raise SystemExit(3)"], 30) is None
    assert executor.executar([sys.executable, "-c", "import time; time.sleep(30)"], 0.5) is None
    assert (executor.executar([sys.executable, "-c", "print('oi')"], 30) or "").strip() == "oi"


def test_sistema_real_le_arquivo_e_lista_pasta(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("conteúdo", encoding="utf-8")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    sistema = SistemaReal()
    assert sistema.ler_texto(str(tmp_path / "b.txt")) == "conteúdo"
    assert sistema.ler_texto(str(tmp_path / "nao-existe")) is None
    assert sistema.listar(str(tmp_path)) == ["a.txt", "b.txt"]
    assert sistema.listar(str(tmp_path / "nao-existe")) == []
    assert sistema.nome in {"windows", "mac", "linux"}


class DetectorFixo(DetectorDeGpus):
    def __init__(self, achadas: tuple[Gpu, ...]) -> None:
        super().__init__(SistemaFalso("linux"), ExecutorFalso({}))
        self.achadas = achadas

    def detectar(self) -> tuple[Gpu, ...]:
        return self.achadas


def maquina_com(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, achadas: tuple[Gpu, ...], nvidia: tuple[str | None, float | None], cuda: bool
) -> hardware.Maquina:
    monkeypatch.setattr(hardware, "_placa_nvidia", lambda: nvidia)
    monkeypatch.setattr(hardware, "_cuda_funciona", lambda: cuda)
    monkeypatch.setattr(hardware, "_bibliotecas_da_cuda_presentes", lambda pasta: True)
    return hardware.detectar(tmp_path, DetectorFixo(achadas))


def test_maquina_com_so_amd_reconhece_a_placa_e_transcreve_na_cpu(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rx = Gpu(Fabricante.AMD, "AMD Radeon RX 6600", 8.0, False)
    maquina = maquina_com(monkeypatch, tmp_path, (rx,), (None, None), cuda=False)
    assert (maquina.dispositivo, maquina.gpu_em_uso, maquina.gpus) == ("cpu", None, (rx,))
    tela = maquina.para_a_tela()
    assert tela["gpus"] == [{"fabricante": "amd", "nome": "AMD Radeon RX 6600", "memoria_gb": 8.0, "integrada": False}]
    assert tela["gpu_em_uso"] is None
    assert tela["gpu_principal"] == tela["gpus"][0]
    assert tela["placa"] is None and tela["dispositivo"] == "cpu"  # o que já existia segue igual


def test_maquina_com_nvidia_usa_a_placa_e_completa_a_memoria_pelo_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    do_windows = Gpu(Fabricante.NVIDIA, "NVIDIA GeForce RTX 4060", None, False)
    amd = Gpu(Fabricante.AMD, "AMD Radeon Graphics", 0.5, True)
    maquina = maquina_com(monkeypatch, tmp_path, (amd, do_windows), ("NVIDIA GeForce RTX 4060 Laptop GPU", 8.0), cuda=True)
    assert maquina.dispositivo == "cuda"
    assert maquina.gpu_em_uso == Gpu(Fabricante.NVIDIA, "NVIDIA GeForce RTX 4060 Laptop GPU", 8.0, False)
    assert len(maquina.gpus) == 2 and maquina.para_a_tela()["placa"] == "NVIDIA GeForce RTX 4060 Laptop GPU"


def test_maquina_com_nvidia_que_a_lista_do_sistema_nao_trouxe_entra_pelo_nvidia_smi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    maquina = maquina_com(monkeypatch, tmp_path, (), ("NVIDIA GeForce GTX 1650", 4.0), cuda=True)
    assert maquina.gpu_em_uso == Gpu(Fabricante.NVIDIA, "NVIDIA GeForce GTX 1650", 4.0, False)


def test_maquina_sem_nenhuma_placa_tem_listas_vazias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tela = maquina_com(monkeypatch, tmp_path, (), (None, None), cuda=False).para_a_tela()
    assert (tela["gpus"], tela["gpu_em_uso"], tela["gpu_principal"]) == ([], None, None)
