import os
import psycopg2
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import QgsVectorLayer, QgsProject, QgsVectorFileWriter
from PyQt5.QtCore import Qt

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'access_clinic_dialog_base.ui'))


class ClinicAccessibilityDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        """Constructor."""
        super().__init__(parent)
        self.setupUi(self)

        # Connect UI elements to functions
        self.populate_districts()
        self.runButton.clicked.connect(self.run_analysis)
        self.exportButton.clicked.connect(self.export_results)
        self.closeButton.clicked.connect(self.close)

        # Placeholder for results layer
        self.results_layer = None

    def connect_to_db(self):
        """Establish a reusable database connection."""
        try:
            return psycopg2.connect(
                dbname="dbAnalysis",
                user="postgres",
                password="user/099",
                host="localhost",
                port="5432"
            )
        except psycopg2.Error as e:
            QMessageBox.critical(self, "Database Error", f"Could not connect to the database:\n{e}")
            return None

    def populate_districts(self):
        """Fetch and populate districts from the database."""
        conn = self.connect_to_db()
        if not conn:
            return

        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT district FROM mw_districts ORDER BY district;")
                districts = [row[0] for row in cur.fetchall()]
                self.districtComboBox.clear()
                self.districtComboBox.addItems(districts)
        except psycopg2.Error as e:
            QMessageBox.critical(self, "Database Error", f"Failed to fetch districts:\n{e.pgerror}")
        finally:
            conn.close()

    def run_analysis(self):
        """Run analysis based on user inputs."""
        selected_district = self.districtComboBox.currentText()
        buffer_distance = self.bufferDistanceSpinBox.value()
        population_threshold = self.populationSpinBox.value()
        analysis_type = self.analysisTypeComboBox.currentText()

        if not selected_district:
            QMessageBox.warning(self, "Input Error", "Please select a district.")
            return

        conn = self.connect_to_db()
        if not conn:
            return

        query = self.build_query(selected_district, buffer_distance, population_threshold, analysis_type)
        self.resultTextBox.append(f"Running analysis for {selected_district} with {buffer_distance}m buffer...")
        self.resultTextBox.append(f"Executing query:\n{query}")
        print(f"Executing query:\n{query}")  # Log query to console

        try:
            with conn.cursor() as cur:
                cur.execute(query)  # Validate query execution

            # Prepare the results layer
            results_layer_path = (
                f"dbname='dbAnalysis' host='localhost' user='postgres' password='user/099' port='5432' "
                f"sql='{query}'"
            )
            self.results_layer = QgsVectorLayer(results_layer_path, f"{selected_district} Analysis", "postgres")

            if self.results_layer.isValid():
                # Add the results layer to the project
                QgsProject.instance().addMapLayer(self.results_layer)
                self.resultTextBox.append("Analysis completed successfully.")
                QMessageBox.information(self, "Success", "Analysis completed successfully.")
            else:
                # Log potential reasons for failure
                self.resultTextBox.append("Failed to load analysis results.")
                self.resultTextBox.append("Possible reasons:")
                self.resultTextBox.append("- Invalid query (check syntax and table names).")
                self.resultTextBox.append("- Database connection issues.")
                self.resultTextBox.append("- CRS mismatch or invalid geometry.")
                print("Layer failed to load. Check query or database connection.")
                QMessageBox.critical(self, "Error", "Failed to load analysis results into QGIS.")
        except psycopg2.Error as e:
            QMessageBox.critical(self, "Query Error", f"Error executing query:\n{e.pgerror}")
            self.resultTextBox.append(f"Query Error:\n{e.pgerror}")
            print(f"SQL Error: {e.pgerror}")
        except Exception as e:
            QMessageBox.critical(self, "Unexpected Error", f"An error occurred:\n{str(e)}")
            self.resultTextBox.append(f"Unexpected Error:\n{str(e)}")
            print(f"Unexpected Error: {str(e)}")
        finally:
            conn.close()

    def build_query(self, district, buffer, population, analysis_type):
        """Construct query based on user inputs."""
        base_query = f"""
        WITH district_schools AS (
            SELECT id, ST_Transform(ST_Buffer(geom, {buffer}), 4326) AS buffer_geom
            FROM mw_schools
            WHERE district = '{district}'
        )
        """
        if analysis_type == "Population Coverage":
            return base_query + f"""
            SELECT p.id, ST_Transform(p.geom, 4326) AS geom
            FROM city_popn p
            JOIN district_schools ds ON ST_Intersects(p.geom, ds.buffer_geom)
            WHERE p.total_pop >= {population};
            """
        elif analysis_type == "Road-Based Accessibility":
            return base_query + f"""
            SELECT r.id, ST_Transform(r.geom, 4326) AS geom
            FROM city_main_roads r
            JOIN district_schools ds ON ST_DWithin(r.geom, ds.buffer_geom, {buffer});
            """
        else:
            # Default to isochrone
            return base_query + f"""
            SELECT id, buffer_geom AS geom FROM district_schools;
            """

    def export_results(self):
        """Export analysis results."""
        if not self.results_layer:
            QMessageBox.warning(self, "Export Error", "No results to export.")
            return

        export_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Results", "", "GeoJSON (*.geojson);;Shapefile (*.shp)"
        )

        if not export_path:
            return

        error = QgsVectorFileWriter.writeAsVectorFormat(
            self.results_layer, export_path, "UTF-8", self.results_layer.crs(), "GeoJSON"
        )

        if error == QgsVectorFileWriter.NoError:
            QMessageBox.information(self, "Export Success", f"Results exported to {export_path}")
            self.resultTextBox.append(f"Results successfully exported to: {export_path}")
        else:
            QMessageBox.critical(self, "Export Error", f"Failed to export results to {export_path}")
            self.resultTextBox.append(f"Export Error: Failed to save to {export_path}")
