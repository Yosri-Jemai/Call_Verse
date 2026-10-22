.PHONY: all start help

SCENARIO ?= normal
MOOD ?= 0
LANGUAGE ?= auto
DIFFICULTY ?= 0
SEED ?=
MAX_TURNS ?= 8
CLIENT_LLM ?= auto
ADVISOR_PORT ?= 8000

all: start

start:
	powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\Agent_Communication\launch_all.ps1" -Scenario "$(SCENARIO)" -Mood "$(MOOD)" -Language "$(LANGUAGE)" -Difficulty "$(DIFFICULTY)" -Seed "$(SEED)" -MaxTurns "$(MAX_TURNS)" -ClientLlm "$(CLIENT_LLM)" -AdvisorPort "$(ADVISOR_PORT)"

help:
	@echo Start Customer Advisor and automatically run the Client Simulator agent against it.
	@echo Example: make start SCENARIO=panne MOOD=-0.6 LANGUAGE=fr
