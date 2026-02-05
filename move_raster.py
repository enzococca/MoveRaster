import os
import shutil
from datetime import datetime
from osgeo import gdal
from qgis.PyQt.QtCore import Qt, QCoreApplication, QSettings, QTranslator, QLocale
from qgis.PyQt.QtGui import QIcon, QAction
from qgis.PyQt.QtWidgets import (
    QMessageBox, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QCheckBox,
    QGroupBox, QAbstractItemView, QComboBox
)
from qgis.gui import QgsMapToolEmitPoint
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsGeometry,
    QgsFeature,
    QgsWkbTypes,
    QgsPointXY,
    Qgis,
    QgsMapLayer
)
from qgis.utils import iface
from .resources import *


def tr(message):
    """Get the translation for a string using Qt translation API."""
    return QCoreApplication.translate('MoveRaster', message)


class MoveRaster:
    """
    Plugin to move raster and associated vector layers together.
    Supports GeoTIFF (internal georeferencing) and images with world files.
    Supports moving only selected geometries.
    Supports undo to restore previous position.
    """

    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.use_center = False
        self.tool = None
        self.undo_stack = []
        self.max_undo_levels = 10

        # Initialize locale and translator
        self.plugin_dir = os.path.dirname(__file__)

        # Check plugin-specific language setting first, then fall back to QGIS locale
        plugin_locale = QSettings().value('MoveRaster/language', '')
        if plugin_locale:
            locale = plugin_locale
        else:
            qgis_locale = QSettings().value('locale/userLocale', 'en')
            locale = qgis_locale[0:2] if qgis_locale else 'en'

        locale_path = os.path.join(
            self.plugin_dir,
            'i18n',
            f'moveraster_{locale}.qm'
        )

        self.translator = None
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

    def initGui(self):
        # Main button - Move
        self.action = QAction(
            QIcon(':/icon/icon.png'),
            tr("Move Layer (Raster + Vector)"),
            self.iface.mainWindow()
        )
        self.action.setToolTip(tr("Move raster and associated geometries"))
        self.action.triggered.connect(self.show_layer_selector)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&MoveRaster", self.action)

        # Undo button
        self.undo_action = QAction(
            QIcon(':/icon/undo.png'),
            tr("Undo Move"),
            self.iface.mainWindow()
        )
        self.undo_action.setToolTip(tr("Restore previous position"))
        self.undo_action.triggered.connect(self.undo_move)
        self.undo_action.setEnabled(False)
        self.iface.addToolBarIcon(self.undo_action)
        self.iface.addPluginToMenu("&MoveRaster", self.undo_action)

        # Toggle corner/center mode
        self.mode_action = QAction(tr("Toggle Mode (Corner/Center)"), self.iface.mainWindow())
        self.mode_action.triggered.connect(self.toggle_mode)
        self.iface.addPluginToMenu("&MoveRaster", self.mode_action)

        # Help button
        self.help_action = QAction(tr("Help"), self.iface.mainWindow())
        self.help_action.triggered.connect(self.show_help)
        self.iface.addPluginToMenu("&MoveRaster", self.help_action)

        # Language selector
        self.language_action = QAction(tr("Language") + " / Lingua", self.iface.mainWindow())
        self.language_action.triggered.connect(self.show_language_selector)
        self.iface.addPluginToMenu("&MoveRaster", self.language_action)

        # Tutorial
        self.tutorial_action = QAction(tr("Tutorial"), self.iface.mainWindow())
        self.tutorial_action.triggered.connect(self.show_tutorial)
        self.iface.addPluginToMenu("&MoveRaster", self.tutorial_action)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        self.iface.removeToolBarIcon(self.undo_action)
        self.iface.removePluginMenu("&MoveRaster", self.action)
        self.iface.removePluginMenu("&MoveRaster", self.undo_action)
        self.iface.removePluginMenu("&MoveRaster", self.mode_action)
        self.iface.removePluginMenu("&MoveRaster", self.help_action)
        self.iface.removePluginMenu("&MoveRaster", self.language_action)
        self.iface.removePluginMenu("&MoveRaster", self.tutorial_action)

        if self.translator:
            QCoreApplication.removeTranslator(self.translator)

    def toggle_mode(self):
        """Toggle between corner and center mode."""
        self.use_center = not self.use_center
        mode = tr("Center") if self.use_center else tr("Upper Left Corner")
        iface.messageBar().pushMessage(
            "MoveRaster",
            tr("Mode: {}").format(mode),
            level=0,
            duration=2
        )

    def show_layer_selector(self):
        """Show dialog to select layers to move together."""
        dialog = LayerSelectorDialog(self.iface, self.use_center)
        if dialog.exec():
            raster_layer = dialog.get_selected_raster()
            vector_layers = dialog.get_selected_vectors()
            self.use_center = dialog.use_center_checkbox.isChecked()
            selected_only = dialog.selected_only_checkbox.isChecked()

            if not raster_layer and not vector_layers:
                QMessageBox.warning(
                    self.iface.mainWindow(),
                    tr("Warning"),
                    tr("Select at least one layer!")
                )
                return

            # Activate move tool
            self.tool = MultiLayerClickTool(
                self.canvas,
                raster_layer,
                vector_layers,
                self.use_center,
                selected_only,
                self.save_state_callback
            )
            self.canvas.setMapTool(self.tool)

            mode = tr("center") if self.use_center else tr("upper left corner")
            sel_info = tr(" (selected only)") if selected_only else ""
            iface.messageBar().pushMessage(
                "MoveRaster",
                tr("Click on the map to move layers (mode: {}){}").format(mode, sel_info),
                level=0,
                duration=5
            )

    def save_state_callback(self, state):
        """Callback called by tool to save state before moving."""
        self.undo_stack.append(state)
        if len(self.undo_stack) > self.max_undo_levels:
            self.undo_stack.pop(0)
        self.undo_action.setEnabled(True)

    def undo_move(self):
        """Undo the last move."""
        if not self.undo_stack:
            QMessageBox.information(
                self.iface.mainWindow(),
                tr("Undo"),
                tr("No move to undo.")
            )
            return

        state = self.undo_stack.pop()

        try:
            # Restore raster
            if state.get('raster'):
                raster_state = state['raster']
                self._restore_raster(raster_state)

            # Restore vectors
            for vector_state in state.get('vectors', []):
                self._restore_vector(vector_state)

            self.canvas.refresh()
            iface.messageBar().pushMessage(
                "MoveRaster",
                tr("Move undone!"),
                level=0,
                duration=3
            )

        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(),
                tr("Undo Error"),
                tr("Error during undo:\n{}").format(str(e))
            )

        self.undo_action.setEnabled(len(self.undo_stack) > 0)

    def _restore_raster(self, raster_state):
        """Restore raster georeferencing."""
        raster_path = raster_state['raster_path']
        layer_name = raster_state['layer_name']
        raster_type = raster_state['raster_type']

        # Remove layer before modifying file
        layers = QgsProject.instance().mapLayersByName(layer_name)
        if layers:
            for layer in layers:
                if layer.type() == QgsMapLayer.LayerType.RasterLayer:
                    QgsProject.instance().removeMapLayer(layer.id())
                    break

        if raster_type == 'geotiff':
            original_geotransform = raster_state['original_geotransform']
            ds = gdal.Open(raster_path, gdal.GA_Update)
            if ds:
                ds.SetGeoTransform(original_geotransform)
                ds.FlushCache()
                ds = None
        else:
            world_path = raster_state['world_file_path']
            world_content = raster_state['world_file_content']
            with open(world_path, 'w') as wf:
                wf.write(world_content)

        iface.addRasterLayer(raster_path, layer_name)

    def _restore_vector(self, vector_state):
        """Restore original geometries of vector layer."""
        layer_id = vector_state['layer_id']
        geometries = vector_state['geometries']

        layer = QgsProject.instance().mapLayer(layer_id)
        if not layer:
            return

        was_editing = layer.isEditable()
        if not was_editing:
            layer.startEditing()

        for fid, wkt in geometries.items():
            geom = QgsGeometry.fromWkt(wkt)
            layer.changeGeometry(int(fid), geom)

        if not was_editing:
            layer.commitChanges()

        layer.triggerRepaint()

    def show_help(self):
        help_text = (
            "═══════════════════════════════════════════\n"
            "   SPOSTARASTER (Raster + Vector) Plugin\n"
            "═══════════════════════════════════════════\n\n"
            + tr("This plugin allows you to move together:") + "\n"
            + tr("  - A raster layer (GeoTIFF or with world file)") + "\n"
            + tr("  - One or more associated vector layers") + "\n"
            + tr("  - Only selected geometries (optional)") + "\n\n"
            + tr("SUPPORTED FORMATS:") + "\n"
            "───────────────────\n"
            + tr("- GeoTIFF (.tif, .tiff) - modifies internal geotransform") + "\n"
            + tr("- PNG/JPG with world file (.pgw, .jgw, .wld)") + "\n"
            + tr("- Other formats with world file") + "\n\n"
            + tr("USAGE:") + "\n"
            "─────────\n"
            + tr("1. Click on 'Move Layer' button") + "\n"
            + tr("2. In the selection window:") + "\n"
            + tr("   - Choose the raster to move") + "\n"
            + tr("   - Select associated vector layers") + "\n"
            + tr("   - Choose mode (corner/center)") + "\n"
            + tr("   - Check 'Selected geometries only' if needed") + "\n"
            + tr("3. Click OK") + "\n"
            + tr("4. Click on the map at destination point") + "\n\n"
            + tr("SELECTED GEOMETRIES ONLY:") + "\n"
            "───────────────────────────\n"
            + tr("If you have selected features in vector layers,") + "\n"
            + tr("you can choose to move only those instead of") + "\n"
            + tr("all geometries in the layer.") + "\n\n"
            + tr("MODE:") + "\n"
            "─────────\n"
            + tr("- Upper Left Corner: clicked point becomes") + "\n"
            + tr("  the upper left corner of the raster") + "\n"
            + tr("- Center: clicked point becomes the center") + "\n"
            + tr("  of the raster") + "\n\n"
            + tr("UNDO:") + "\n"
            "────────\n"
            + tr("Click 'Undo Move' to restore") + "\n"
            + tr("previous position (up to 10 levels).") + "\n\n"
            + tr("NOTE: Files are modified directly.") + "\n"
            + tr("Backup is recommended!")
        )
        QMessageBox.information(
            self.iface.mainWindow(),
            tr("Help - MoveRaster"),
            help_text
        )

    def show_language_selector(self):
        """Show dialog to select plugin language."""
        dialog = LanguageSelectorDialog(self.iface)
        if dialog.exec():
            selected_lang = dialog.get_selected_language()
            QSettings().setValue('MoveRaster/language', selected_lang)
            QMessageBox.information(
                self.iface.mainWindow(),
                tr("Language Changed"),
                tr("Language changed to: {}").format(dialog.get_language_name(selected_lang)) + "\n\n" +
                tr("Please reload the plugin for changes to take effect.") + "\n" +
                "(Plugins > Plugin Reloader)"
            )

    def show_tutorial(self):
        """Show interactive tutorial for the plugin."""
        dialog = TutorialDialog(self.iface)
        dialog.exec()


class LanguageSelectorDialog(QDialog):
    """Dialog to select plugin language."""

    LANGUAGES = {
        'it': 'Italiano',
        'en': 'English'
    }

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle(tr("Select Language") + " / Seleziona Lingua")
        self.setMinimumWidth(300)

        layout = QVBoxLayout()

        # Language label
        label = QLabel(tr("Choose language:") + " / Scegli la lingua:")
        layout.addWidget(label)

        # Language combo box
        self.language_combo = QComboBox()
        current_lang = QSettings().value('MoveRaster/language', '')

        for code, name in self.LANGUAGES.items():
            self.language_combo.addItem(name, code)
            if code == current_lang:
                self.language_combo.setCurrentIndex(self.language_combo.count() - 1)

        layout.addWidget(self.language_combo)

        # Info label
        info_label = QLabel(tr("Note: Reload plugin after changing language.") + "\n" +
                           "Nota: Ricaricare il plugin dopo aver cambiato lingua.")
        info_label.setStyleSheet("color: #666666; font-size: 10px;")
        layout.addWidget(info_label)

        # Buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton(tr("OK"))
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton(tr("Cancel") + " / Annulla")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def get_selected_language(self):
        return self.language_combo.currentData()

    def get_language_name(self, code):
        return self.LANGUAGES.get(code, code)


class TutorialDialog(QDialog):
    """Interactive tutorial dialog."""

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.current_step = 0

        # Tutorial steps - bilingual
        self.steps = self._get_tutorial_steps()

        self.setWindowTitle(tr("Tutorial - MoveRaster"))
        self.setMinimumWidth(550)
        self.setMinimumHeight(400)

        layout = QVBoxLayout()

        # Step indicator
        self.step_label = QLabel()
        self.step_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #333333;")
        layout.addWidget(self.step_label)

        # Content area
        self.content_label = QLabel()
        self.content_label.setWordWrap(True)
        self.content_label.setStyleSheet("font-size: 12px; padding: 10px; background-color: #f0f0f0; color: #333333; border: 1px solid #cccccc; border-radius: 5px;")
        self.content_label.setMinimumHeight(250)
        layout.addWidget(self.content_label)

        # Navigation buttons
        button_layout = QHBoxLayout()

        self.prev_btn = QPushButton(tr("Previous") + " / Precedente")
        self.prev_btn.clicked.connect(self.prev_step)
        button_layout.addWidget(self.prev_btn)

        self.next_btn = QPushButton(tr("Next") + " / Successivo")
        self.next_btn.clicked.connect(self.next_step)
        button_layout.addWidget(self.next_btn)

        self.close_btn = QPushButton(tr("Close") + " / Chiudi")
        self.close_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

        self._update_display()

    def _get_tutorial_steps(self):
        """Return tutorial steps based on current language."""
        lang = QSettings().value('MoveRaster/language', '')
        if not lang:
            qgis_locale = QSettings().value('locale/userLocale', 'en')
            lang = qgis_locale[0:2] if qgis_locale else 'en'

        if lang == 'it':
            return [
                {
                    'title': '👋 Benvenuto in MoveRaster!',
                    'content': (
                        "Questo plugin ti permette di spostare layer raster e vettoriali "
                        "semplicemente cliccando sulla mappa.\n\n"
                        "È utile per:\n"
                        "• Georeferenziare immagini non georeferenziate\n"
                        "• Correggere la posizione di raster esistenti\n"
                        "• Spostare insieme raster e vettoriali associati\n\n"
                        "Clicca 'Successivo' per continuare il tutorial."
                    )
                },
                {
                    'title': '📂 Passo 1: Seleziona i Layer',
                    'content': (
                        "Clicca sul pulsante 'Sposta Layer' nella toolbar.\n\n"
                        "Si aprirà una finestra dove potrai:\n\n"
                        "1. LAYER RASTER: Seleziona il raster da spostare. "
                        "Verrà usato come riferimento per calcolare lo spostamento.\n\n"
                        "2. LAYER VETTORIALI: Seleziona uno o più layer vettoriali "
                        "da spostare insieme al raster. Usa 'Seleziona Tutti' per comodità.\n\n"
                        "3. OPZIONI:\n"
                        "   • Modalità angolo/centro\n"
                        "   • Solo geometrie selezionate"
                    )
                },
                {
                    'title': '🎯 Passo 2: Scegli la Modalità',
                    'content': (
                        "Hai due modalità di spostamento:\n\n"
                        "📍 ANGOLO SUPERIORE SINISTRO (default):\n"
                        "Il punto dove clicchi diventerà l'angolo superiore sinistro del raster.\n\n"
                        "📍 CENTRO:\n"
                        "Il punto dove clicchi diventerà il centro del raster.\n\n"
                        "Scegli la modalità in base alle tue esigenze. "
                        "Se hai un punto di riferimento noto (es. un angolo), usa 'Angolo'. "
                        "Se vuoi centrare il raster su un punto, usa 'Centro'."
                    )
                },
                {
                    'title': '🖱️ Passo 3: Clicca sulla Mappa',
                    'content': (
                        "Dopo aver cliccato OK nella finestra di selezione:\n\n"
                        "1. Il cursore cambierà indicando che lo strumento è attivo\n\n"
                        "2. Clicca sulla mappa nel punto di DESTINAZIONE\n\n"
                        "3. Apparirà una finestra di conferma con:\n"
                        "   • Offset X e Y calcolati\n"
                        "   • Tipo di raster (GeoTIFF o World File)\n"
                        "   • Numero di geometrie coinvolte\n\n"
                        "4. Conferma per eseguire lo spostamento"
                    )
                },
                {
                    'title': '↩️ Passo 4: Annulla se Necessario',
                    'content': (
                        "Hai fatto un errore? Nessun problema!\n\n"
                        "Clicca sul pulsante 'Annulla Spostamento' per ripristinare "
                        "la posizione precedente.\n\n"
                        "Il plugin supporta fino a 10 LIVELLI DI UNDO, quindi puoi "
                        "annullare più spostamenti consecutivi.\n\n"
                        "⚠️ IMPORTANTE:\n"
                        "I file vengono modificati direttamente su disco. "
                        "Si consiglia sempre di fare un backup prima di operazioni importanti!"
                    )
                },
                {
                    'title': '📋 Formati Supportati',
                    'content': (
                        "Il plugin supporta diversi formati raster:\n\n"
                        "🖼️ GeoTIFF (.tif, .tiff):\n"
                        "Modifica il geotransform interno al file. "
                        "Non serve un file esterno.\n\n"
                        "🖼️ Immagini con World File:\n"
                        "• PNG → .pgw\n"
                        "• JPG/JPEG → .jgw\n"
                        "• TIFF senza georef → .tfw\n"
                        "• Altri formati → .wld\n\n"
                        "Il world file contiene i parametri di georeferenziazione "
                        "e viene creato/modificato automaticamente."
                    )
                },
                {
                    'title': '✅ Hai Completato il Tutorial!',
                    'content': (
                        "Ora sai come usare MoveRaster!\n\n"
                        "RIASSUNTO:\n"
                        "1. Clicca 'Sposta Layer'\n"
                        "2. Seleziona raster e vettoriali\n"
                        "3. Scegli modalità (angolo/centro)\n"
                        "4. Clicca sulla mappa\n"
                        "5. Conferma lo spostamento\n\n"
                        "💡 SUGGERIMENTO:\n"
                        "Usa 'Solo geometrie selezionate' se vuoi spostare "
                        "solo alcune feature dei layer vettoriali.\n\n"
                        "Buon lavoro! 🎉"
                    )
                }
            ]
        else:
            # English
            return [
                {
                    'title': '👋 Welcome to MoveRaster!',
                    'content': (
                        "This plugin allows you to move raster and vector layers "
                        "simply by clicking on the map.\n\n"
                        "It's useful for:\n"
                        "• Georeferencing non-georeferenced images\n"
                        "• Correcting the position of existing rasters\n"
                        "• Moving rasters and associated vectors together\n\n"
                        "Click 'Next' to continue the tutorial."
                    )
                },
                {
                    'title': '📂 Step 1: Select Layers',
                    'content': (
                        "Click the 'Move Layer' button in the toolbar.\n\n"
                        "A window will open where you can:\n\n"
                        "1. RASTER LAYER: Select the raster to move. "
                        "It will be used as reference for calculating the offset.\n\n"
                        "2. VECTOR LAYERS: Select one or more vector layers "
                        "to move together with the raster. Use 'Select All' for convenience.\n\n"
                        "3. OPTIONS:\n"
                        "   • Corner/center mode\n"
                        "   • Selected geometries only"
                    )
                },
                {
                    'title': '🎯 Step 2: Choose the Mode',
                    'content': (
                        "You have two movement modes:\n\n"
                        "📍 UPPER LEFT CORNER (default):\n"
                        "The point where you click will become the upper left corner of the raster.\n\n"
                        "📍 CENTER:\n"
                        "The point where you click will become the center of the raster.\n\n"
                        "Choose the mode based on your needs. "
                        "If you have a known reference point (e.g., a corner), use 'Corner'. "
                        "If you want to center the raster on a point, use 'Center'."
                    )
                },
                {
                    'title': '🖱️ Step 3: Click on the Map',
                    'content': (
                        "After clicking OK in the selection window:\n\n"
                        "1. The cursor will change indicating the tool is active\n\n"
                        "2. Click on the map at the DESTINATION point\n\n"
                        "3. A confirmation window will appear with:\n"
                        "   • Calculated X and Y offset\n"
                        "   • Raster type (GeoTIFF or World File)\n"
                        "   • Number of geometries involved\n\n"
                        "4. Confirm to execute the move"
                    )
                },
                {
                    'title': '↩️ Step 4: Undo if Needed',
                    'content': (
                        "Made a mistake? No problem!\n\n"
                        "Click the 'Undo Move' button to restore "
                        "the previous position.\n\n"
                        "The plugin supports up to 10 UNDO LEVELS, so you can "
                        "undo multiple consecutive moves.\n\n"
                        "⚠️ IMPORTANT:\n"
                        "Files are modified directly on disk. "
                        "It's always recommended to make a backup before important operations!"
                    )
                },
                {
                    'title': '📋 Supported Formats',
                    'content': (
                        "The plugin supports various raster formats:\n\n"
                        "🖼️ GeoTIFF (.tif, .tiff):\n"
                        "Modifies the internal geotransform. "
                        "No external file needed.\n\n"
                        "🖼️ Images with World File:\n"
                        "• PNG → .pgw\n"
                        "• JPG/JPEG → .jgw\n"
                        "• TIFF without georef → .tfw\n"
                        "• Other formats → .wld\n\n"
                        "The world file contains georeferencing parameters "
                        "and is created/modified automatically."
                    )
                },
                {
                    'title': '✅ Tutorial Completed!',
                    'content': (
                        "Now you know how to use MoveRaster!\n\n"
                        "SUMMARY:\n"
                        "1. Click 'Move Layer'\n"
                        "2. Select raster and vectors\n"
                        "3. Choose mode (corner/center)\n"
                        "4. Click on the map\n"
                        "5. Confirm the move\n\n"
                        "💡 TIP:\n"
                        "Use 'Selected geometries only' if you want to move "
                        "only some features of vector layers.\n\n"
                        "Happy mapping! 🎉"
                    )
                }
            ]

    def _update_display(self):
        """Update the display for current step."""
        step = self.steps[self.current_step]
        self.step_label.setText(f"{step['title']} ({self.current_step + 1}/{len(self.steps)})")
        self.content_label.setText(step['content'])

        self.prev_btn.setEnabled(self.current_step > 0)
        self.next_btn.setEnabled(self.current_step < len(self.steps) - 1)

    def next_step(self):
        if self.current_step < len(self.steps) - 1:
            self.current_step += 1
            self._update_display()

    def prev_step(self):
        if self.current_step > 0:
            self.current_step -= 1
            self._update_display()


class LayerSelectorDialog(QDialog):
    """Dialog to select which layers to move together."""

    def __init__(self, iface, use_center=False):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle(tr("Select Layers to Move"))
        self.setMinimumWidth(450)
        self.setMinimumHeight(550)

        layout = QVBoxLayout()

        # Raster group
        raster_group = QGroupBox(tr("Raster Layer (reference)"))
        raster_layout = QVBoxLayout()

        self.raster_list = QListWidget()
        self.raster_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._populate_raster_list()
        raster_layout.addWidget(self.raster_list)
        raster_group.setLayout(raster_layout)
        layout.addWidget(raster_group)

        # Vector group
        vector_group = QGroupBox(tr("Vector Layers (to move together)"))
        vector_layout = QVBoxLayout()

        self.vector_list = QListWidget()
        self.vector_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._populate_vector_list()
        vector_layout.addWidget(self.vector_list)

        # Quick selection buttons
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton(tr("Select All"))
        select_all_btn.clicked.connect(self.vector_list.selectAll)
        deselect_all_btn = QPushButton(tr("Deselect All"))
        deselect_all_btn.clicked.connect(self.vector_list.clearSelection)
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(deselect_all_btn)
        vector_layout.addLayout(btn_layout)

        vector_group.setLayout(vector_layout)
        layout.addWidget(vector_group)

        # Options
        options_group = QGroupBox(tr("Options"))
        options_layout = QVBoxLayout()

        self.use_center_checkbox = QCheckBox(tr("Use raster center (instead of upper left corner)"))
        self.use_center_checkbox.setChecked(use_center)
        options_layout.addWidget(self.use_center_checkbox)

        # Checkbox for selected geometries only
        self.selected_only_checkbox = QCheckBox(tr("Move selected geometries only"))
        self.selected_only_checkbox.setToolTip(
            tr("If active, moves only selected features in vector layers.") + "\n"
            + tr("Otherwise moves all geometries of selected layers.")
        )

        # Count selected geometries
        total_selected = self._count_selected_features()
        if total_selected > 0:
            self.selected_only_checkbox.setText(
                tr("Move selected geometries only ({} selected)").format(total_selected)
            )
            self.selected_only_checkbox.setEnabled(True)
        else:
            self.selected_only_checkbox.setText(
                tr("Move selected geometries only (no active selection)")
            )
            self.selected_only_checkbox.setEnabled(False)
            self.selected_only_checkbox.setChecked(False)

        options_layout.addWidget(self.selected_only_checkbox)

        # Update checkbox when layer selection changes
        self.vector_list.itemSelectionChanged.connect(self._update_selected_checkbox)

        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        # OK/Cancel buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton(tr("OK"))
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton(tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _count_selected_features(self):
        """Count total selected features in all vector layers."""
        total = 0
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == QgsMapLayer.LayerType.VectorLayer:
                total += layer.selectedFeatureCount()
        return total

    def _count_selected_in_chosen_layers(self):
        """Count selected features in chosen vector layers in dialog."""
        total = 0
        for item in self.vector_list.selectedItems():
            layer_id = item.data(Qt.ItemDataRole.UserRole)
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer:
                total += layer.selectedFeatureCount()
        return total

    def _update_selected_checkbox(self):
        """Update checkbox state based on selected layers."""
        selected_count = self._count_selected_in_chosen_layers()
        if selected_count > 0:
            self.selected_only_checkbox.setText(
                tr("Move selected geometries only ({} selected)").format(selected_count)
            )
            self.selected_only_checkbox.setEnabled(True)
        else:
            self.selected_only_checkbox.setText(
                tr("Move selected geometries only (no selection in chosen layers)")
            )
            self.selected_only_checkbox.setEnabled(False)
            self.selected_only_checkbox.setChecked(False)

    def _populate_raster_list(self):
        """Populate the raster layer list."""
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == QgsMapLayer.LayerType.RasterLayer:
                raster_path = layer.dataProvider().dataSourceUri().split("|")[0]
                raster_type = self._get_raster_type(raster_path)
                item = QListWidgetItem(f"{layer.name()} [{raster_type}]")
                item.setData(Qt.ItemDataRole.UserRole, layer.id())
                self.raster_list.addItem(item)

        # Select active layer if it's a raster
        active = self.iface.activeLayer()
        if active and active.type() == QgsMapLayer.LayerType.RasterLayer:
            for i in range(self.raster_list.count()):
                if self.raster_list.item(i).data(Qt.ItemDataRole.UserRole) == active.id():
                    self.raster_list.setCurrentRow(i)
                    break

    def _get_raster_type(self, raster_path):
        """Determine raster type (GeoTIFF or world file)."""
        ext = os.path.splitext(raster_path)[1].lower()
        if ext in ['.tif', '.tiff']:
            ds = gdal.Open(raster_path)
            if ds:
                gt = ds.GetGeoTransform()
                ds = None
                if gt and gt != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
                    return "GeoTIFF"
        return "World File"

    def _populate_vector_list(self):
        """Populate the vector layer list."""
        for layer in QgsProject.instance().mapLayers().values():
            if layer.type() == QgsMapLayer.LayerType.VectorLayer:
                selected_count = layer.selectedFeatureCount()
                total_count = layer.featureCount()

                if selected_count > 0:
                    label = f"{layer.name()} [{selected_count}/{total_count} " + tr("selected") + "]"
                else:
                    label = f"{layer.name()} [{total_count} features]"

                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, layer.id())
                self.vector_list.addItem(item)

    def get_selected_raster(self):
        """Return the selected raster layer."""
        items = self.raster_list.selectedItems()
        if items:
            layer_id = items[0].data(Qt.ItemDataRole.UserRole)
            return QgsProject.instance().mapLayer(layer_id)
        return None

    def get_selected_vectors(self):
        """Return the list of selected vector layers."""
        layers = []
        for item in self.vector_list.selectedItems():
            layer_id = item.data(Qt.ItemDataRole.UserRole)
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer:
                layers.append(layer)
        return layers


class MultiLayerClickTool(QgsMapToolEmitPoint):
    """Tool to move raster and vector layers together."""

    def __init__(self, canvas, raster_layer, vector_layers, use_center, selected_only, save_callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.raster_layer = raster_layer
        self.vector_layers = vector_layers
        self.use_center = use_center
        self.selected_only = selected_only
        self.save_callback = save_callback

        # Calculate reference extent (from raster or vectors)
        if raster_layer:
            self.reference_extent = raster_layer.extent()
            self.raster_path = raster_layer.dataProvider().dataSourceUri().split("|")[0]
            self.raster_width = raster_layer.width()
            self.raster_height = raster_layer.height()
            self.pixel_size_x = self.reference_extent.width() / self.raster_width
            self.pixel_size_y = -self.reference_extent.height() / self.raster_height

            self.raster_type = self._detect_raster_type()
        else:
            self.reference_extent = self._get_combined_extent()
            self.raster_path = None
            self.raster_type = None

    def _detect_raster_type(self):
        """Detect if raster is GeoTIFF or uses world file."""
        ext = os.path.splitext(self.raster_path)[1].lower()
        if ext in ['.tif', '.tiff']:
            ds = gdal.Open(self.raster_path)
            if ds:
                gt = ds.GetGeoTransform()
                ds = None
                if gt and gt != (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
                    return 'geotiff'
        return 'worldfile'

    def _get_combined_extent(self):
        """Calculate combined extent of all vector layers."""
        extent = None
        for layer in self.vector_layers:
            if extent is None:
                extent = layer.extent()
            else:
                extent.combineExtentWith(layer.extent())
        return extent

    def _count_features_to_move(self):
        """Count features that will be moved."""
        total = 0
        for layer in self.vector_layers:
            if self.selected_only:
                total += layer.selectedFeatureCount()
            else:
                total += layer.featureCount()
        return total

    def canvasReleaseEvent(self, event):
        point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos())

        if not self.reference_extent:
            iface.messageBar().pushMessage(
                tr("Error"),
                tr("No reference extent!"),
                level=2,
                duration=3
            )
            self.canvas.unsetMapTool(self)
            return

        # Calculate current reference point
        if self.use_center:
            ref_x = self.reference_extent.center().x()
            ref_y = self.reference_extent.center().y()
        else:
            ref_x = self.reference_extent.xMinimum()
            ref_y = self.reference_extent.yMaximum()

        # Calculate offset
        offset_x = point.x() - ref_x
        offset_y = point.y() - ref_y

        # Prepare confirmation info
        layer_count = len(self.vector_layers) + (1 if self.raster_layer else 0)
        raster_info = f"\n" + tr("Raster type: {}").format(self.raster_type.upper()) if self.raster_layer else ""

        features_to_move = self._count_features_to_move()
        selection_info = ""
        if self.selected_only:
            selection_info = "\n\n" + tr("Selected geometries only: {} features").format(features_to_move)
        else:
            selection_info = "\n\n" + tr("Total geometries: {} features").format(features_to_move)

        reply = QMessageBox.question(
            self.canvas.window(),
            tr("Confirm Move"),
            tr("Move {} layers?").format(layer_count) + "\n\n"
            f"Offset X: {offset_x:.2f}\n"
            f"Offset Y: {offset_y:.2f}"
            f"{raster_info}"
            f"{selection_info}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.No:
            self.canvas.unsetMapTool(self)
            return

        # Save state for undo BEFORE modifying
        state = self._save_current_state()
        self.save_callback(state)

        # Execute move
        success = True

        # Move raster
        if self.raster_layer:
            success = self._move_raster(offset_x, offset_y) and success

        # Move vectors
        for layer in self.vector_layers:
            success = self._move_vector(layer, offset_x, offset_y) and success

        self.canvas.refresh()

        if success:
            sel_info = tr(" (selected only)") if self.selected_only else ""
            iface.messageBar().pushMessage(
                "MoveRaster",
                tr("{} layers moved successfully!{}").format(layer_count, sel_info),
                level=0,
                duration=3
            )
        else:
            iface.messageBar().pushMessage(
                "MoveRaster",
                tr("Some layers may not have been moved correctly"),
                level=1,
                duration=5
            )

        self.canvas.unsetMapTool(self)

    def _save_current_state(self):
        """Save current state to allow undo."""
        state = {
            'timestamp': datetime.now().isoformat(),
            'raster': None,
            'vectors': [],
            'selected_only': self.selected_only
        }

        # Save raster state
        if self.raster_layer:
            state['raster'] = {
                'layer_name': self.raster_layer.name(),
                'raster_path': self.raster_path,
                'raster_type': self.raster_type
            }

            if self.raster_type == 'geotiff':
                ds = gdal.Open(self.raster_path)
                if ds:
                    state['raster']['original_geotransform'] = ds.GetGeoTransform()
                    ds = None
            else:
                world_path = self._get_world_file_path()
                world_content = ""
                if os.path.exists(world_path):
                    with open(world_path, 'r') as wf:
                        world_content = wf.read()
                state['raster']['world_file_path'] = world_path
                state['raster']['world_file_content'] = world_content

        # Save vector state (only geometries that will be moved)
        for layer in self.vector_layers:
            geometries = {}

            if self.selected_only:
                for feature in layer.selectedFeatures():
                    geom = feature.geometry()
                    if geom and not geom.isEmpty():
                        geometries[str(feature.id())] = geom.asWkt()
            else:
                for feature in layer.getFeatures():
                    geom = feature.geometry()
                    if geom and not geom.isEmpty():
                        geometries[str(feature.id())] = geom.asWkt()

            state['vectors'].append({
                'layer_id': layer.id(),
                'layer_name': layer.name(),
                'geometries': geometries
            })

        return state

    def _get_world_file_path(self):
        """Determine world file path."""
        base, ext = os.path.splitext(self.raster_path)
        ext = ext.lower()

        world_ext_map = {
            '.tif': '.tfw',
            '.tiff': '.tfw',
            '.png': '.pgw',
            '.jpg': '.jgw',
            '.jpeg': '.jgw',
            '.gif': '.gfw',
            '.bmp': '.bpw'
        }

        if ext in world_ext_map:
            world_ext = world_ext_map[ext]
        elif len(ext) > 3:
            world_ext = f".{ext[1]}{ext[3]}w"
        else:
            world_ext = ".wld"

        return base + world_ext

    def _move_raster(self, offset_x, offset_y):
        """Move raster (GeoTIFF or world file)."""
        try:
            new_x_ul = self.reference_extent.xMinimum() + offset_x
            new_y_ul = self.reference_extent.yMaximum() + offset_y

            layer_name = self.raster_layer.name()
            QgsProject.instance().removeMapLayer(self.raster_layer.id())

            if self.raster_type == 'geotiff':
                success = self._move_geotiff(new_x_ul, new_y_ul)
            else:
                success = self._move_worldfile(new_x_ul, new_y_ul)

            if success:
                iface.addRasterLayer(self.raster_path, layer_name)

            return success

        except Exception as e:
            QMessageBox.critical(
                self.canvas.window(),
                tr("Raster Error"),
                tr("Error moving raster:\n{}").format(str(e))
            )
            return False

    def _move_geotiff(self, new_x_ul, new_y_ul):
        """Move GeoTIFF by modifying internal geotransform."""
        try:
            ds = gdal.Open(self.raster_path, gdal.GA_Update)
            if not ds:
                raise Exception(tr("Cannot open {} in write mode").format(self.raster_path))

            new_geotransform = (
                new_x_ul,
                self.pixel_size_x,
                0.0,
                new_y_ul,
                0.0,
                self.pixel_size_y
            )

            ds.SetGeoTransform(new_geotransform)
            ds.FlushCache()
            ds = None

            return True

        except Exception as e:
            QMessageBox.critical(
                self.canvas.window(),
                tr("GeoTIFF Error"),
                tr("Error modifying GeoTIFF:\n{}").format(str(e))
            )
            return False

    def _move_worldfile(self, new_x_ul, new_y_ul):
        """Move raster by modifying world file."""
        try:
            world_path = self._get_world_file_path()

            with open(world_path, 'w') as wf:
                wf.write(f"{self.pixel_size_x}\n")
                wf.write("0.0\n")
                wf.write("0.0\n")
                wf.write(f"{self.pixel_size_y}\n")
                wf.write(f"{new_x_ul}\n")
                wf.write(f"{new_y_ul}\n")

            return True

        except Exception as e:
            QMessageBox.critical(
                self.canvas.window(),
                tr("World File Error"),
                tr("Error modifying world file:\n{}").format(str(e))
            )
            return False

    def _move_vector(self, layer, offset_x, offset_y):
        """Move vector layer by translating geometries."""
        try:
            was_editing = layer.isEditable()
            if not was_editing:
                layer.startEditing()

            if self.selected_only:
                for feature in layer.selectedFeatures():
                    geom = feature.geometry()
                    if geom and not geom.isEmpty():
                        geom.translate(offset_x, offset_y)
                        layer.changeGeometry(feature.id(), geom)
            else:
                for feature in layer.getFeatures():
                    geom = feature.geometry()
                    if geom and not geom.isEmpty():
                        geom.translate(offset_x, offset_y)
                        layer.changeGeometry(feature.id(), geom)

            if not was_editing:
                layer.commitChanges()

            layer.triggerRepaint()
            return True

        except Exception as e:
            QMessageBox.critical(
                self.canvas.window(),
                tr("Vector Error"),
                tr("Error moving layer {}:\n{}").format(layer.name(), str(e))
            )
            if not was_editing and layer.isEditable():
                layer.rollbackChanges()
            return False
