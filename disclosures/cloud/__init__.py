"""Everything that depends on which cloud the system runs in.

The rest of the code talks to three small interfaces and never imports a cloud SDK:

    storage.BlobStore      copies data files to and from a bucket
    warehouse.Warehouse    runs SQL over the insider-trade table
    llm.chat_model         returns the language model, as a LangChain chat model

Each module holds the interface, the implementations that exist today, and a factory that
picks one from the settings. Porting to another cloud means adding an implementation here
(for example Azure Blob Storage, Azure OpenAI and Synapse) and rewriting `infra/`. Nothing
outside this package and `infra/` should have to change.
"""
