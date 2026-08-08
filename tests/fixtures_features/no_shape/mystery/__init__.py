"""Neither shape: no `register`, and only half of the legacy pair. The loader
must crash the boot over it rather than skip it.
"""
from aiogram import Router

router = Router()
