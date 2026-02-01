; Ruby symbol extraction

(class
  name: (constant) @name.definition.class) @definition.class

(module
  name: (constant) @name.definition.module) @definition.module

(method
  name: (identifier) @name.definition.function) @definition.function

(singleton_method
  name: (identifier) @name.definition.function) @definition.function

(assignment
  left: (constant) @name.definition.constant) @definition.constant

(call
  method: (identifier) @name.reference.call) @reference.call

(call
  receiver: (constant) @name.reference.call
  method: (identifier) @name.reference.call) @reference.call

(require
  (string (string_content) @name.reference.import)) @reference.import

(require_relative
  (string (string_content) @name.reference.import)) @reference.import

(superclass
  (constant) @name.reference.inherit) @reference.inherit

(superclass
  (scope_resolution
    name: (constant) @name.reference.inherit)) @reference.inherit
