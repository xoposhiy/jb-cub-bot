"""No `register`, and a `router` where one used to be enough. The loader must
crash the boot over it rather than skip it -- and a package still written to
the retired shape is exactly the mistake worth crashing over.
"""
from aiogram import Router

router = Router()
