# Prueba de extremo a extremo

Recorre con Chromium real el registro, la verificación de correo, la inscripción con código, la
aprobación y una clase en vivo completa con tres estudiantes. Requiere el prototipo en
`127.0.0.1:8100` con el correo en consola.

```bash
pip install playwright                     # usa el Chromium del sistema, no descarga navegadores
./reiniciar-demo.sh                        # ¡borra la base! solo en el prototipo
python e2e.py                              # capturas en ./capturas/
```
