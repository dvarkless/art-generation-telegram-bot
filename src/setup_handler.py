import logging
import logging.handlers


def get_handler():
    # create rotating file handler with 3 files, each limited to 3 MB
    handler = logging.handlers.RotatingFileHandler(
        'log.log', maxBytes=3*1024*1024, backupCount=3)
    handler.setLevel(logging.DEBUG)

# create formatter
    formatter = logging.Formatter(
        "%(asctime)s in %(name)s: %(levelname)s MESSAGE:'%(message)s")

# add formatter to handler
    handler.setFormatter(formatter)

    return handler
