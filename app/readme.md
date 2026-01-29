app/
  main.py                      # FastAPI + startup/shutdown
  config.py                    # settings/env
  logging_conf.py              # logging (opcional)

  domain/
    models.py                  # Driver, Dispatch, Order (dataclasses/pydantic)
    menu_data.py               # MENU (data)
  
  repositories/
    dispatch_repo.py           # ACTIVE_DISPATCHES / DRIVER_ACTIVE encapsulados
    driver_repo.py             # DRIVERS encapsulado
    menu_repo.py               # acceso a MENU

  services/
    pricing_service.py         # price_order (puro)
    dispatch_service.py        # asignación, reasignación, formateo msg, estado

  llm/
    tools.py                   # @tool wrappers (healthcheck, get_menu, price_order,...)
    agent_factory.py           # build_agent()

  adapters/
    telegram_client.py         # wrapper para tg_app.bot.send_message
    secrets.py                 # load_secret_as_env

  application/
    telegram_router.py         # on_text (router), handle_driver_message, run_agent
