Veeam API Reporter - Automated Infrastructure Monitoring

Aquesta solució automatitza la generació d'informes executius de rendiment i capacitat per a infraestructures Veeam Backup & Replication. Mitjançant l'ús de l'API REST de Veeam, scripts de processament en Python i orquestració amb Ansible, permet obtenir visibilitat total sobre l'estat dels backups sense necessitat de revisar la consola manualment.

Funcionalitats principals
Extracció de dades via API: Utilitza l'API REST de Veeam (v1.3+) per consultar sessions de BackupJob, BackupCopyJob, AgentBackup i HealthCheck.

Processament multihil: Script Python optimitzat amb ThreadPoolExecutor per processar centenars de sessions en pocs segons.

Informes HTML Executius: Generació automàtica d'informes HTML amb:

Mètriques agregades (volum de dades, temps de procés).

Desglossament per entorns (PRO, PRE, ITG).

Mapes de calor d'activitat diària.

Top 10 de jobs amb més consum de volum i temps.

Alerta via SMTP: Enviament automatitzat de correus electrònics amb suport per a autenticació i xifratge (TLS).

Arquitectura
Ansible Playbooks: Orquestren la connexió a l'API de Veeam, gestionen l'autenticació (OAuth2) i descarreguen les dades en format JSON.

Scripts Python: Processen els fitxers JSON, calculen les mètriques de volum i temps, i construeixen el cos del missatge en HTML.

Notificació: Enviament del report final via servidor SMTP.

Requisits
Ansible (per executar els playbooks d'orquestració).

Python 3.x (amb les llibreries estàndard json, smtplib, ssl, urllib).

Accés a la Veeam REST API (per defecte port 9419).

Instal·lació ràpida
Clona aquest repositori.

Configura les variables d'entorn al teu fitxer d'inventari d'Ansible o al fitxer .env:

VEEAM_API_URL: L'URL del teu servidor Veeam.

VEEAM_PASS: Password de l'usuari amb permisos de lectura (Veeam Backup Viewer).

SMTP_HOST / SMTP_USER / SMTP_PASS: Paràmetres del teu servidor de correu.

Executa el playbook corresponent:

Bash
ansible-playbook WIN_Veeam_Monthly_reports.yml
Llicència
Aquest projecte està sota la Llicència MIT. Pots utilitzar-lo, modificar-lo i distribuir-lo lliurement, sempre que mantinguis l'avís d'autoria original.
