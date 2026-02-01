; Haskell symbol extraction

(function
  name: (variable) @name.definition.function) @definition.function

(signature
  name: (variable) @name.definition.function) @definition.function

(adt
  name: (type) @name.definition.class) @definition.class

(newtype
  name: (type) @name.definition.class) @definition.class

(class
  name: (type) @name.definition.interface) @definition.interface

(instance
  class: (type) @name.reference.inherit) @reference.inherit

(type_alias
  name: (type) @name.definition.type) @definition.type

(apply
  (variable) @name.reference.call) @reference.call

(import
  module: (module) @name.reference.import) @reference.import

(qualified_variable
  module: (module)
  variable: (variable) @name.reference.call) @reference.call
