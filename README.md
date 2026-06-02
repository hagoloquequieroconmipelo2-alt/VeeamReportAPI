<div align="center">
  <h1>📊 Veeam API Reporter</h1>
  <p><b>Automated Infrastructure Monitoring & Executive Reporting</b></p>

  <img src="https://img.shields.io/badge/python-3.x-blue.svg" alt="Python 3.x">
  <img src="https://img.shields.io/badge/ansible-ready-red.svg" alt="Ansible">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT">
</div>

---

Aquesta solució automatitza la generació d'informes executius de rendiment i capacitat per a infraestructures **Veeam Backup & Replication**. Mitjançant l'ús de l'API REST de Veeam, scripts de processament en Python i orquestració amb Ansible, permet obtenir visibilitat total sobre l'estat dels backups sense necessitat de revisar la consola manualment.

## ✨ Funcionalitats Principals

- 🔌 **Extracció de dades via API**: Utilitza l'API REST de Veeam (v1.3+) per consultar sessions de `BackupJob`, `BackupCopyJob`, `AgentBackup` i `HealthCheck`.
- ⚡ **Processament Multihil**: Script Python optimitzat amb `ThreadPoolExecutor` per processar centenars de sessions en pocs segons.
- 📈 **Informes HTML Executius**: Generació automàtica d'informes rics i visuals amb:
  - Mètriques agregades (volum de dades, temps de procés).
  - Desglossament per entorns (PRO, PRE, ITG).
  - Mapes de calor d'activitat diària.
  - *Top 10* de jobs amb més consum de volum i temps.
- 📧 **Alerta via SMTP**: Enviament automatitzat de correus electrònics amb suport per a autenticació i xifratge (TLS).

## 🏗️ Arquitectura

1. **Ansible Playbooks**: Orquestren la connexió a l'API de Veeam, gestionen l'autenticació (OAuth2) i descarreguen les dades en format JSON.
2. **Scripts Python**: Processen els fitxers JSON, calculen les mètriques de volum i temps, i construeixen el cos del missatge en HTML.
3. **Notificació**: Enviament del report final via servidor SMTP directament a la safata de l'equip de TI.

## 🛠️ Requisits

Per executar aquest projecte necessitaràs:
- **Ansible** (per executar els *playbooks* d'orquestració).
- **Python 3.x** (amb les llibreries estàndard `json`, `smtplib`, `ssl`, `urllib`).
- Accés a la **Veeam REST API** (per defecte al port `9419`).

## 🚀 Instal·lació i Ús

**1. Clonar el repositori**
```bash
git clone [https://github.com/hagoloquequieroconmipelo2-alt/VeeamReportAPI.git](https://github.com/hagoloquequieroconmipelo2-alt/VeeamReportAPI.git)
cd VeeamReportAPI
