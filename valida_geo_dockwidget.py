# -*- coding: utf-8 -*-
import os

from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal

from qgis.core import (QgsProject, QgsVectorLayer, Qgis, QgsMessageLog, 
                       QgsSpatialIndex, QgsFeature, QgsField, QgsFields,
                       QgsVectorLayerUtils, QgsFeatureRequest, QgsWkbTypes,
                       QgsGeometry, QgsTask, QgsApplication) # Novas importações
from qgis import processing

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'valida_geo_dockwidget_base.ui'))


# ==========================================================
# ▼▼▼ NOVA CLASSE PARA PROCESSAMENTO EM SEGUNDO PLANO ▼▼▼
# ==========================================================
class CorrectionTask(QgsTask):
    """
    Esta tarefa executa todo o processo de correção em uma thread separada
    para não congelar a interface do QGIS.
    """
    def __init__(self, description, source_layer, errors_to_fix):
        super().__init__(description, QgsTask.CanCancel)
        self.source_layer = source_layer
        self.errors_to_fix = errors_to_fix
        self.corrected_layer = None
        self.summary_message = "Nenhuma correção foi aplicada."

    def run(self):
        """O código que executa em segundo plano."""
        try:
            # ... (Toda a lógica de 'correct_errors' foi movida para cá) ...
            fids_to_correct_geometry = self.errors_to_fix['geom']
            fids_with_overlaps = self.errors_to_fix['sobrep']
            fids_to_delete_duplicates = self.errors_to_fix['duplic']
            overlap_pairs = self.errors_to_fix['sobrep_pairs']

            corrections_applied_tags = []
            if fids_to_correct_geometry: corrections_applied_tags.append("geom")
            if fids_to_delete_duplicates: corrections_applied_tags.append("duplic")
            if fids_with_overlaps: corrections_applied_tags.append("sobrep")

            if not corrections_applied_tags:
                return True

            suffix = "_corrigida_" + "_".join(corrections_applied_tags)
            new_layer_name = f"{self.source_layer.name()}{suffix}"
            self.corrected_layer = self.source_layer.clone()
            self.corrected_layer.setName(new_layer_name)
            self.corrected_layer.startEditing()
            
            geometries_corrected = 0
            if fids_to_correct_geometry:
                for fid in fids_to_correct_geometry:
                    if self.isCanceled(): return False
                    geom = self.source_layer.getFeature(fid).geometry().makeValid()
                    if self.corrected_layer.changeGeometry(fid, geom): geometries_corrected += 1
            
            duplicates_deleted = 0
            if fids_to_delete_duplicates:
                if self.isCanceled(): return False
                self.corrected_layer.dataProvider().deleteFeatures(fids_to_delete_duplicates)
                duplicates_deleted = len(fids_to_delete_duplicates)

            overlaps_corrected_groups = self.correct_overlaps(self.corrected_layer, overlap_pairs)
            
            self.corrected_layer.commitChanges()

            self.summary_message = f"Correção concluída. Geometrias: {geometries_corrected}. Duplicatas: {duplicates_deleted}. Grupos de sobreposição unidos: {overlaps_corrected_groups}."
            return True
        except Exception as e:
            self.exception = e
            return False

    def finished(self, result):
        """Executa quando a tarefa termina. É seguro interagir com a UI."""
        if result:
            QgsProject.instance().addMapLayer(self.corrected_layer)
            QgsApplication.messageBar().pushMessage("Sucesso", self.summary_message, level=Qgis.Success, duration=10)
        else:
            if self.exception:
                QgsApplication.messageBar().pushMessage("Erro", f"Ocorreu um erro na correção: {self.exception}", level=Qgis.Critical, duration=10)

    def correct_overlaps(self, layer, overlap_pairs):
        if not overlap_pairs: return 0
        
        # Algoritmo de Agrupamento Rápido (Teoria dos Grafos)
        adj = {}
        for u, v in overlap_pairs:
            adj.setdefault(u, []).append(v)
            adj.setdefault(v, []).append(u)
        
        visited = set()
        groups = []
        nodes = list(adj.keys())
        for node in nodes:
            if node not in visited:
                current_group = []
                q = [node]
                visited.add(node)
                while q:
                    curr = q.pop(0)
                    current_group.append(curr)
                    for neighbor in adj.get(curr, []):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            q.append(neighbor)
                groups.append(current_group)
        
        # Para cada grupo, une as geometrias
        features_to_add = []
        fids_to_delete = []
        for i, group_fids in enumerate(groups):
            if self.isCanceled(): return -1 # Permite cancelar
            self.setProgress((i / len(groups)) * 100) # Atualiza a barra de progresso

            if not group_fids: continue
            fids_to_delete.extend(group_fids)
            geometries_to_union = []
            
            request = QgsFeatureRequest().setFilterFids(group_fids)
            for feature in layer.getFeatures(request):
                geom = feature.geometry()
                if not geom.isValid(): geom = geom.makeValid()
                geometries_to_union.append(geom)

            if geometries_to_union:
                dissolved_geometry = QgsGeometry.unaryUnion(geometries_to_union)
                new_feature = QgsFeature(layer.fields())
                new_feature.setGeometry(dissolved_geometry)
                new_feature.setAttributes(layer.getFeature(group_fids[0]).attributes())
                features_to_add.append(new_feature)

        if fids_to_delete: layer.dataProvider().deleteFeatures(fids_to_delete)
        if features_to_add: layer.dataProvider().addFeatures(features_to_add)

        return len(groups)


class ValidaGeoDockWidget(QtWidgets.QDockWidget, FORM_CLASS):

    closingPlugin = pyqtSignal()
    # ... (O resto da classe continua aqui) ...
    def __init__(self, iface, parent=None):
        super(ValidaGeoDockWidget, self).__init__(parent)
        self.iface = iface; self.setupUi(self)
        self.validateButton.clicked.connect(self.run_validation_process)
        self.errorsTableWidget.cellClicked.connect(self.zoom_to_feature_from_table)
        self.correctAllButton.clicked.connect(self.run_correction_task)
        self.populate_layer_combobox()
    def closeEvent(self, event): self.closingPlugin.emit(); event.accept()
    def populate_layer_combobox(self):
        self.layerComboBox.clear(); layers = QgsProject.instance().mapLayers().values()
        for layer in layers:
            if isinstance(layer, QgsVectorLayer): self.layerComboBox.addItem(layer.name(), layer)
    def run_validation_process(self):
        selected_layer = self.layerComboBox.currentData()
        if not selected_layer: self.iface.messageBar().pushMessage("Aviso", "Nenhuma camada vetorial selecionada.", level=Qgis.Warning, duration=3); return
        self.errorsTableWidget.setRowCount(0); check_geometry = self.geometryCheckBox.isChecked(); check_overlaps = self.overlapsCheckBox.isChecked(); check_duplicates = self.duplicatesCheckBox.isChecked()
        self.iface.messageBar().pushMessage("Info", f"Iniciando validação para a camada: {selected_layer.name()}", level=Qgis.Info, duration=4)
        if check_geometry: self.validate_geometry(selected_layer)
        if check_overlaps: self.validate_overlaps(selected_layer)
        if check_duplicates: self.validate_duplicates(selected_layer)
        self.iface.messageBar().pushMessage("Concluído", "Processo de validação finalizado.", level=Qgis.Info, duration=4)
    def validate_geometry(self, layer):
        QgsMessageLog.logMessage(f"Executando verificação de geometria para '{layer.name()}'", 'ValidaGeo', level=Qgis.Info); params = {'INPUT_LAYER': layer, 'METHOD': 0, 'VALID_OUTPUT': 'memory:','INVALID_OUTPUT': 'memory:','ERROR_OUTPUT': 'memory:'}; result = processing.run("qgis:checkvalidity", params); invalid_layer = result['INVALID_OUTPUT']; error_count = 0
        for feature in invalid_layer.getFeatures():
            error_count += 1; row_position = self.errorsTableWidget.rowCount(); self.errorsTableWidget.insertRow(row_position); feature_id = str(feature.id())
            self.errorsTableWidget.setItem(row_position, 0, QtWidgets.QTableWidgetItem(feature_id)); self.errorsTableWidget.setItem(row_position, 1, QtWidgets.QTableWidgetItem("Geometria Inválida")); self.errorsTableWidget.setItem(row_position, 2, QtWidgets.QTableWidgetItem("A geometria da feição não é válida."))
        if error_count > 0: self.iface.messageBar().pushMessage("Info", f"Geometria: Encontrados {error_count} erros.", level=Qgis.Info, duration=5)
    def validate_overlaps(self, layer):
        QgsMessageLog.logMessage(f"Executando verificação de sobreposições para '{layer.name()}'", 'ValidaGeo', level=Qgis.Info); all_features = {f.id(): f for f in layer.getFeatures()}; index = QgsSpatialIndex(layer.getFeatures()); error_count = 0; reported_pairs = set()
        for feature_id, feature in all_features.items():
            candidate_ids = index.intersects(feature.geometry().boundingBox())
            for candidate_id in candidate_ids:
                if feature_id >= candidate_id: continue
                candidate_feature = all_features.get(candidate_id)
                if not candidate_feature: continue
                if feature.geometry().intersects(candidate_feature.geometry()):
                    pair = tuple(sorted((feature_id, candidate_id)));
                    if pair in reported_pairs: continue
                    error_count += 1; reported_pairs.add(pair); row_position = self.errorsTableWidget.rowCount(); self.errorsTableWidget.insertRow(row_position)
                    self.errorsTableWidget.setItem(row_position, 0, QtWidgets.QTableWidgetItem(str(feature_id))); self.errorsTableWidget.setItem(row_position, 1, QtWidgets.QTableWidgetItem("Sobreposição")); self.errorsTableWidget.setItem(row_position, 2, QtWidgets.QTableWidgetItem(f"Sobrepõe a feição ID {candidate_id}"))
        self.iface.messageBar().pushMessage("Info", f"Sobreposição: Encontrados {error_count} erros.", level=Qgis.Info, duration=5)
    def validate_duplicates(self, layer):
        QgsMessageLog.logMessage(f"Executando verificação de duplicatas para '{layer.name()}'", 'ValidaGeo', level=Qgis.Info); geometries_seen = set(); duplicates_to_report = []
        for feature in layer.getFeatures():
            geom_wkb = feature.geometry().asWkb()
            if geom_wkb in geometries_seen: duplicates_to_report.append(feature.id())
            else: geometries_seen.add(geom_wkb)
        for fid in duplicates_to_report:
            row_position = self.errorsTableWidget.rowCount(); self.errorsTableWidget.insertRow(row_position)
            self.errorsTableWidget.setItem(row_position, 0, QtWidgets.QTableWidgetItem(str(fid))); self.errorsTableWidget.setItem(row_position, 1, QtWidgets.QTableWidgetItem("Duplicata")); self.errorsTableWidget.setItem(row_position, 2, QtWidgets.QTableWidgetItem("A geometria desta feição é idêntica à de uma anterior."))
        self.iface.messageBar().pushMessage("Info", f"Duplicatas: Encontradas {len(duplicates_to_report)} feições duplicadas.", level=Qgis.Info, duration=5)
    def zoom_to_feature_from_table(self, row, column):
        layer = self.layerComboBox.currentData();
        if not layer: return
        id_item = self.errorsTableWidget.item(row, 0)
        if not id_item: return
        try:
            feature_id = int(id_item.text()); layer.selectByIds([feature_id])
            self.iface.mapCanvas().zoomToSelected(layer)
        except (ValueError, TypeError): print(f"Não foi possível converter o ID da feição para número: {id_item.text()}")
    
    def run_correction_task(self):
        """Prepara e inicia a tarefa de correção em segundo plano."""
        source_layer = self.layerComboBox.currentData()
        if not source_layer or self.errorsTableWidget.rowCount() == 0:
            self.iface.messageBar().pushMessage("Aviso", "Nenhuma camada ou nenhum erro na tabela para corrigir.", level=Qgis.Warning, duration=3)
            return

        # Coleta todos os erros da tabela
        errors = {'geom': [], 'sobrep': set(), 'duplic': [], 'sobrep_pairs': []}
        for row in range(self.errorsTableWidget.rowCount()):
            error_type = self.errorsTableWidget.item(row, 1).text()
            feature_id = int(self.errorsTableWidget.item(row, 0).text())
            
            if error_type == "Geometria Inválida": errors['geom'].append(feature_id)
            elif error_type == "Duplicata": errors['duplic'].append(feature_id)
            elif error_type == "Sobreposição":
                errors['sobrep'].add(feature_id)
                description = self.errorsTableWidget.item(row, 2).text()
                other_id = int(description.split()[-1])
                errors['sobrep'].add(other_id)
                errors['sobrep_pairs'].append((feature_id, other_id))
        
        # Cria e inicia a tarefa
        task = CorrectionTask(f"Corrigindo '{source_layer.name()}'", source_layer, errors)
        QgsApplication.taskManager().addTask(task)