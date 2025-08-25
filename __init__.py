# -*- coding: utf-8 -*-

def classFactory(iface):
    """
    Carrega a classe principal do plugin a partir do arquivo valida_geo.py.
    """
    # ==========================================================
    # ▼▼▼ A CORREÇÃO ESTÁ NA LINHA ABAIXO ▼▼▼
    # Trocamos 'plugin_main' pelo nome correto do nosso arquivo: 'valida_geo'
    # ==========================================================
    from .valida_geo import ValidaGeo
    return ValidaGeo(iface)