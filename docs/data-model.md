# Modelo de datos propuesto

## Observaciones

- El archivo contiene una hoja por `CICLO` y todas comparten el mismo layout.
- Los indicadores SEMS no varian por plantel dentro de la misma `ENTIDAD` y `CICLO`.
- Hay texto con problemas de codificacion que debe normalizarse durante la importacion.

## Colecciones

### `catalog_entidades`

- `clave`
- `nombre`

### `catalog_municipios`

- `entidad_clave`
- `clave`
- `nombre`

Indice natural:
- `entidad_clave + clave`

### `catalog_localidades`

- `entidad_clave`
- `municipio_clave`
- `clave`
- `nombre`

Indice natural:
- `entidad_clave + municipio_clave + clave`

### `catalog_opciones`

- `tipo`
- `clave`
- `valor`

Usos previstos:
- `control`
- `modalidad`
- `subcontrol`
- `nivel`
- `subnivel`
- `sigla`
- `ciclo`

### `planteles`

- `plantel_cct`
- `escuela_clave`
- `nombre`
- `entidad_clave`
- `municipio_clave`
- `localidad_clave`
- `control`
- `modalidad`
- `subcontrol`
- `nivel`
- `subnivel`
- `sigla`

Indices naturales:
- `plantel_cct`
- `escuela_clave`

### `matricula_plantel_ciclo`

- `plantel_cct`
- `ciclo`
- `escuelas`
- `alumnos`
- `mujeres`
- `hombres`
- `docentes`
- `docentes_m`
- `docentes_h`

Indice natural:
- `plantel_cct + ciclo`

### `indicadores_entidad_ciclo`

- `entidad_clave`
- `ciclo`
- `cobertura`
- `tasa_absorcion`
- `tasa_abandono`
- `tasa_reprobacion`
- `tasa_neta_escolarizacion`
- `tasa_terminacion`

Indice natural:
- `entidad_clave + ciclo`

## Reglas de transformacion

- Las llaves geograficas deben conservarse como texto para no perder ceros a la izquierda.
- Los valores enteros deben convertirse a `int` cuando existan.
- Los porcentajes deben convertirse a `float`.
- Los textos deben pasar por una normalizacion basica para corregir mojibake frecuente.
- Si una clave viene vacia, el documento debe guardarse sin ese segmento catalogico.

## Carga inicial

El importador separa:

- catalogos geograficos
- catalogos de opciones
- planteles
- matricula por plantel y ciclo
- indicadores por entidad y ciclo

Comando previsto:

```bash
./educadata_env/bin/python manage.py import_educadata \
  --path "/Users/azaelcupa/Downloads/Bases filtros_EducaData_040326_eiol.xlsx"
```

Para reemplazar un ciclo puntual sin dejar registros obsoletos:

```bash
./educadata_env/bin/python manage.py import_educadata \
  --path "/Users/azaelcupa/Downloads/Ciclo 2024-2025.xlsx" \
  --cycle 2024-2025 \
  --replace-cycles
```
