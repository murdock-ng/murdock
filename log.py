import logging

log = logging.getLogger('')
log.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

handler = logging.StreamHandler()
handler.setFormatter(formatter)

log.addHandler(handler)
