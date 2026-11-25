extends Control

@onready var http = $HTTPRequest
@onready var email_input = $Email
@onready var pass_input = $Password
@onready var status_label = $Status

# TROCA ISTO PELO TEU LINK DO RENDER
const API_URL = "https://pitlaneparty.onrender.com"

func _on_LoginButton_pressed():
	var dados = {"email": email_input.text, "password": pass_input.text}
	var json = JSON.stringify(dados)
	http.request(API_URL + "/login", ["Content-Type: application/json"], HTTPClient.METHOD_POST, json)
	status_label.text = "A entrar..."

func _on_HTTPRequest_request_completed(result, response_code, headers, body):
	if response_code == 200:
		var dados = JSON.parse_string(body.get_string_from_utf8())
		status_label.text = "Bem-vindo! Pontos: " + str(dados["pontos"])
	else:
		status_label.text = "Login falhou. Tenta de novo."
