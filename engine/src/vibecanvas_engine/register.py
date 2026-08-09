from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class BaseLLM(ABC):
    """
    Unified abstract base class for Large Language Models.
    """

    @abstractmethod
    def __call__(
        self,
        conversation_dict: Dict[str, Any],
        inference_config: Optional[Dict[str, Any]] = None,
        stop_event: Optional[Any] = None,
    ) -> str:
        """
        Unified interface for LLM inference calls.

        :param conversation_dict: A dictionary containing multimodal conversations in ShareGPT format.
            Format example:
            {
                 "conversations": [
                     {"from": "human", "value": "Please describe this image and this audio"},
                     {"from": "gpt", "value": "..."}
                 ],
                 "image": ["path/or/url/to/image.jpg"],
                 "video": ["path/or/url/to/video.mp4"],
                 "audio": ["path/or/url/to/audio.wav"]
            }
        :param inference_config: A dictionary of inference hyperparameters, e.g., {"temperature": 0.7, "max_tokens": 2048}.
        :param stop_event: Optional cancellation signal (threading.Event or multiprocessing.Event)
            propagated by the workflow engine. Implementations are expected to honor it by
            aborting in-flight HTTP requests as soon as it is set — typical patterns:
              * spin a watcher thread that calls ``client.close()`` on the underlying httpx
                client when ``stop_event.is_set()``;
              * pass a short request timeout and re-issue inside a poll loop that checks the
                event between attempts;
              * for streaming responses, break out of the iterator on each chunk.
            Implementations that cannot cooperate should at least check the event before
            and after the call and raise ``RuntimeError("LLM call cancelled")`` so the
            engine can surface the cancellation cleanly.
        :return: The generation result of the model (the return format can be specifically defined in subclasses according to business needs).
        :raises NotImplementedError: Forces subclasses to implement this method.
        """
        raise NotImplementedError("Subclasses must implement the __call__ method to execute inference logic!")

class Registry:
    """
    A universal class registry.
    """
    def __init__(self, name="DefaultRegistry"):
        self.name = name
        self._module_dict = {}

    def register(self, name=None):
        """
        Decorator factory function for registering classes.
        :param name: The alias for registration. If not provided, defaults to the class's __name__.
        """
        def decorator(cls):
            # Use the class's own name if no name is specified
            register_name = name if name is not None else cls.__name__

            # Prevent duplicate registration
            if register_name in self._module_dict:
                raise KeyError(f"In {self.name}, the name '{register_name}' has already been registered!")

            # Save the class into the dictionary
            self._module_dict[register_name] = cls
            return cls

        return decorator

    def get(self, name):
        """
        Get the corresponding class by name.
        :param name: The name used during registration.
        :return: The corresponding class.
        """
        if name not in self._module_dict:
            raise KeyError(f"Cannot find a class named '{name}' in {self.name}. Please check if it has been registered correctly.")
        return self._module_dict[name]

    def list_all(self):
        """
        Get a list of all registered class names.
        """
        return list(self._module_dict.keys())


# Instantiate a global model registry object
# All subsequent model classes will be decorated with this object
llm_registry = Registry(name="LLMRegistry")

# Instantiate a global node registry object
# Each concrete node class should be decorated with `@node_registry.register()`
# so that the workflow engine can resolve `node_type` strings into classes
# without relying on `eval` or wildcard imports.
node_registry = Registry(name="NodeRegistry")
