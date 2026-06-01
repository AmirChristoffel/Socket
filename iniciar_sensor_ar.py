# iniciar_sensor_ar.py
from dispositivos import SensorControlavel

if __name__ == "__main__":
    sensor_ar = SensorControlavel(tipo='AIR_QUALITY_SENSOR', data_unit='ug/m3')
    sensor_ar.iniciar()
