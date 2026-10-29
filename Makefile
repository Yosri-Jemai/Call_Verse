.PHONY: all start help

ADVISOR_PORT ?= 8000
CLIENT_API_PORT ?= 8001
UI_PORT ?= 3000

all: start

start:
	powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\Agent_Communication\launch_all.ps1" -AdvisorPort "$(ADVISOR_PORT)" -ClientApiPort "$(CLIENT_API_PORT)" -UiPort "$(UI_PORT)"

help:
	@echo Start Customer Advisor, Client Simulator API, and Relay dashboard.
	@echo Example: make start ADVISOR_PORT=8010 UI_PORT=3001
